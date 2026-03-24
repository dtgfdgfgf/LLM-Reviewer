"""
Strict LLM-native review pipeline.

This module adds a second review path optimized for fully LLM-generated repos.
It combines deterministic planning + verification with orthogonal specialist
agents, a challenger pass for disputed findings, and a judge that emits a
blocking verdict with structured outputs.
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from copilot.generated.session_events import SessionEventType
from copilot.types import SessionConfig, Tool, ToolInvocation, ToolResult

from backend.logging_config import get_logger
from backend.orchestration.event_bus import EventBus
from backend.orchestration.model_router import AgentRole, ModelRouter
from backend.orchestration.report_artifacts import (
    ArtifactSummary,
    SessionMetrics,
    SessionReport,
    build_artifact_summary,
    build_challenger_session_report,
    build_final_summary_markdown,
    build_judge_session_report,
    build_next_steps_markdown,
    build_strict_session_report,
    role_display_name,
    verification_check_title,
    verification_status_label,
)
from backend.orchestration.review_store import ReviewStore
from backend.orchestration.session_manager import SessionManager
from backend.orchestration.strict_types import (
    ChallengeDecision,
    ChallengeSubmission,
    ConvergenceMetrics,
    DriftSummary,
    DriftType,
    EvidenceRef,
    Finding,
    FindingKind,
    FindingSeverity,
    FindingSubmission,
    GateVerdict,
    JudgmentSubmission,
    LLMReviewPlan,
    ReviewProfile,
    SpecialistAssignment,
    VerificationApplicability,
    VerificationCheckResult,
    VerificationRole,
    VerificationSummary,
)
from backend.orchestration.verification import run_verification
from backend.tools.codebase import build_codebase_tools, is_supported_text_file

logger = get_logger("strict_pipeline")

_STRICT_CONTEXT_WINDOW = 200_000
_PRIMARY_BUDGET_RATIO = 0.60
_JUDGE_BUDGET_RATIO = 0.70
_PRIMARY_TIMEOUT_S = 480.0
_CHALLENGER_TIMEOUT_S = 300.0
_JUDGE_TIMEOUT_S = 300.0
_LIVENESS_TIMEOUT_S = 90.0
_WATCHDOG_POLL_S = 10.0

_ROLE_MODELS: dict[str, AgentRole] = {
    "spec_drift": AgentRole.SPEC_DRIFT,
    "architecture_integrity": AgentRole.ARCHITECTURE_INTEGRITY,
    "security_boundary": AgentRole.SECURITY_BOUNDARY,
    "runtime_operational": AgentRole.RUNTIME_OPERATIONAL,
    "test_integrity": AgentRole.TEST_INTEGRITY,
    "llm_artifact_simplification": AgentRole.LLM_ARTIFACT_SIMPLIFICATION,
    "challenger": AgentRole.CHALLENGER,
    "judge": AgentRole.JUDGE,
}

_ROLE_DISPLAY_NAMES: dict[str, str] = {
    "spec_drift": "規格漂移",
    "architecture_integrity": "架構完整性",
    "security_boundary": "安全邊界",
    "runtime_operational": "執行期與營運",
    "test_integrity": "測試完整性",
    "llm_artifact_simplification": "LLM 產物與簡化",
    "challenger": "挑戰者",
    "judge": "最終裁決",
}

_ROLE_FOCUS: dict[str, str] = {
    "spec_drift": (
        "Compare code, tests, config, and docs against the stated intent. Flag spec drift, "
        "missing traceability, and places where implementation silently changes behavior."
    ),
    "architecture_integrity": (
        "Audit boundaries, ownership, layering, and systemic coherence. Name where the design "
        "drifts from its own architecture or overfits to imagined future needs."
    ),
    "security_boundary": (
        "Audit trust boundaries, auth/authz, secrets, unsafe defaults, and places where generated "
        "code assumes security guarantees it does not actually enforce."
    ),
    "runtime_operational": (
        "Audit runtime assumptions, CI/build behavior, dependency realism, failure handling, and "
        "operational risk. Treat missing runnable evidence as a first-class concern. Discover the "
        "validation set across CI, task runners, root/subproject manifests, docs, and touched "
        "subsystems before treating any failed command as repo-wide breakage."
    ),
    "test_integrity": (
        "Audit whether tests validate behavior or merely mirror implementation. "
        "Flag brittle tests, missing behavior coverage, and spec/test drift. "
        "Separate runtime failures from coverage gaps, validate whether integration "
        "labels actually cross real boundaries, and only elevate missing system-level "
        "validation to a product or security defect when the repo explicitly claims "
        "that guarantee."
    ),
    "llm_artifact_simplification": (
        "Audit LLM-specific smells: over-abstraction, fake extensibility, hallucinated APIs, "
        "repeated generated patterns, needless indirection, and unnecessary complexity."
    ),
}

_SPECIALIST_PATTERNS: dict[str, tuple[str, ...]] = {
    "spec_drift": ("spec", "adr", "readme", "docs/", "api", "schema", "openapi", "test"),
    "architecture_integrity": ("src/", "app/", "backend", "frontend", "service", "domain"),
    "security_boundary": ("auth", "security", "permission", "token", "secret", "config", "api"),
    "runtime_operational": ("ci", "workflow", "docker", "compose", "config", "main", "app"),
    "test_integrity": ("test", "__tests__", "spec.", "fixture", "mock"),
    "llm_artifact_simplification": ("src/", "app/", "service", "util", "helper", "config"),
}


@dataclass
class StrictReviewOutcome:
    report: str
    verdict: GateVerdict
    findings: list[Finding]
    consensus_findings: list[Finding]
    disputed_findings: list[Finding]
    convergence_metrics: ConvergenceMetrics
    verification_summary: VerificationSummary
    drift_summary: DriftSummary
    session_reports: list[SessionReport]
    final_summary_markdown: str
    next_steps_markdown: str
    artifact_summary: ArtifactSummary


@dataclass
class SpecialistRunResult:
    submission: FindingSubmission
    session_report: SessionReport


@dataclass
class ChallengeRunResult:
    decisions: list[ChallengeDecision]
    session_report: SessionReport


@dataclass
class JudgeRunResult:
    submission: JudgmentSubmission
    session_report: SessionReport


def _inline_schema_refs(schema: dict) -> dict:
    schema = copy.deepcopy(schema)
    defs = schema.pop("$defs", {})

    def _resolve(obj: Any) -> Any:
        if isinstance(obj, dict):
            if "$ref" in obj:
                ref_name = obj["$ref"].split("/")[-1]
                return _resolve(defs[ref_name])
            return {k: _resolve(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_resolve(item) for item in obj]
        return obj

    return _resolve(schema)


class BudgetManager:
    def __init__(
        self,
        context_window: int = _STRICT_CONTEXT_WINDOW,
        primary_ratio: float = _PRIMARY_BUDGET_RATIO,
        judge_ratio: float = _JUDGE_BUDGET_RATIO,
    ) -> None:
        self._context_window = context_window
        self._primary_limit = int(context_window * primary_ratio)
        self._judge_limit = int(context_window * judge_ratio)

    @property
    def primary_limit(self) -> int:
        return self._primary_limit

    @property
    def judge_limit(self) -> int:
        return self._judge_limit

    def estimate_assignment_tokens(
        self,
        root: Path,
        assignment: SpecialistAssignment,
        verification_slice: list[dict[str, Any]],
    ) -> int:
        total_chars = 0
        for rel_path in {
            *assignment.shared_core_files,
            *assignment.artifact_files,
            *assignment.role_extra_files,
        }:
            try:
                total_chars += (root / rel_path).stat().st_size
            except OSError:
                continue
        total_chars += len(json.dumps(verification_slice, ensure_ascii=True))
        total_chars += len(assignment.focus) + sum(len(item) for item in assignment.risk_hypotheses)
        return max(2_000, total_chars // 4 + 2_000)

    def shard_assignment(
        self,
        root: Path,
        assignment: SpecialistAssignment,
        verification_slice: list[dict[str, Any]],
    ) -> list[SpecialistAssignment]:
        estimated = self.estimate_assignment_tokens(root, assignment, verification_slice)
        assignment.estimated_tokens = estimated
        if estimated <= self.primary_limit or len(assignment.role_extra_files) <= 1:
            return [assignment]

        shard_size = max(1, len(assignment.role_extra_files) // 2)
        shards: list[SpecialistAssignment] = []
        chunks = [
            assignment.role_extra_files[i : i + shard_size]
            for i in range(0, len(assignment.role_extra_files), shard_size)
        ]
        total = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            shard = assignment.model_copy(
                update={
                    "agent_id": f"{assignment.agent_id}__{index}",
                    "display_name": f"{assignment.display_name} {index}/{total}",
                    "role_extra_files": chunk,
                    "shard_index": index,
                    "shard_count": total,
                }
            )
            shard.estimated_tokens = self.estimate_assignment_tokens(
                root, shard, verification_slice
            )
            shards.append(shard)
        return shards


class StrictSessionAgent:
    """Session runner for strict-mode agents with dynamic IDs."""

    def __init__(
        self,
        *,
        session: Any,
        event_bus: EventBus,
        review_id: str,
        agent_id: str,
        display_name: str,
        base_role: str,
        model: str,
        timeout_s: float,
    ) -> None:
        self._session = session
        self._event_bus = event_bus
        self._review_id = review_id
        self._agent_id = agent_id
        self._display_name = display_name
        self._base_role = base_role
        self._model = model
        self._timeout_s = timeout_s
        self._metrics: dict[str, Any] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "turns": 0,
        }
        self._tool_call_count = 0
        self._status = "idle"
        self._started_at_ms: int | None = None
        self._completed_at_ms: int | None = None
        self._last_activity = time.monotonic()

    async def run(self, prompt: str) -> str:
        loop = asyncio.get_running_loop()
        self._started_at_ms = int(time.time() * 1000)
        self._completed_at_ms = None
        self._status = "running"
        self._tool_call_count = 0
        await self._publish(
            {
                "type": "agent.started",
                "agent": self._agent_id,
                "display_name": self._display_name,
                "base_role": self._base_role,
                "model": self._model,
            }
        )

        async def _async_on_event(event: Any) -> None:
            self._last_activity = time.monotonic()
            etype = event.type

            if etype == SessionEventType.ASSISTANT_MESSAGE_DELTA and event.data.delta_content:
                await self._publish(
                    {
                        "type": "agent.stream",
                        "agent": self._agent_id,
                        "display_name": self._display_name,
                        "content": event.data.delta_content,
                    }
                )
            elif etype == SessionEventType.TOOL_EXECUTION_START:
                self._tool_call_count += 1
                await self._publish(
                    {
                        "type": "agent.tool_call",
                        "agent": self._agent_id,
                        "display_name": self._display_name,
                        "tool_name": event.data.tool_name or "unknown",
                        "tool_call_id": event.data.tool_call_id or "",
                        "args": event.data.arguments,
                    }
                )
            elif etype == SessionEventType.TOOL_EXECUTION_COMPLETE:
                await self._publish(
                    {
                        "type": "agent.tool_result",
                        "agent": self._agent_id,
                        "display_name": self._display_name,
                        "tool_name": event.data.tool_name or "unknown",
                        "tool_call_id": event.data.tool_call_id or "",
                        "success": True,
                    }
                )
            elif etype == SessionEventType.ASSISTANT_USAGE:
                self._metrics["input_tokens"] += event.data.input_tokens or 0
                self._metrics["output_tokens"] += event.data.output_tokens or 0
                self._metrics["cache_read_tokens"] += event.data.cache_read_tokens or 0
                self._metrics["cache_write_tokens"] += event.data.cache_write_tokens or 0
                self._metrics["turns"] += 1
                await self._publish(
                    {
                        "type": "metrics.update",
                        "agent": self._agent_id,
                        "display_name": self._display_name,
                        "base_role": self._base_role,
                        "model": event.data.model or self._model,
                        **self._metrics,
                    }
                )

        def on_event(event: Any) -> None:
            loop.call_soon_threadsafe(asyncio.ensure_future, _async_on_event(event))

        unsubscribe = self._session.on(on_event)
        start_time = time.monotonic()
        session_task = asyncio.create_task(
            self._session.send_and_wait({"prompt": prompt}, timeout=self._timeout_s)
        )
        watchdog_task = asyncio.create_task(self._watchdog(start_time))
        try:
            done, pending = await asyncio.wait(
                [session_task, watchdog_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            if session_task not in done:
                reason = watchdog_task.result()
                raise TimeoutError(reason)

            event = session_task.result()
            duration_ms = int((time.monotonic() - start_time) * 1000)
            self._completed_at_ms = int(time.time() * 1000)
            self._status = "complete"
            await self._publish(
                {
                    "type": "agent.done",
                    "agent": self._agent_id,
                    "display_name": self._display_name,
                    "duration_ms": duration_ms,
                }
            )
            return event.data.content if event and event.data and event.data.content else ""
        except Exception as exc:
            self._completed_at_ms = int(time.time() * 1000)
            self._status = "error"
            await self._publish(
                {
                    "type": "agent.error",
                    "agent": self._agent_id,
                    "display_name": self._display_name,
                    "error": str(exc),
                }
            )
            return ""
        finally:
            unsubscribe()
            await self._session.destroy()

    async def _watchdog(self, start_time: float) -> str:
        while True:
            await asyncio.sleep(_WATCHDOG_POLL_S)
            now = time.monotonic()
            if now - start_time > self._timeout_s:
                return f"Exceeded hard timeout of {int(self._timeout_s)}s"
            if now - self._last_activity > _LIVENESS_TIMEOUT_S:
                return f"No activity for {int(now - self._last_activity)}s"

    async def _publish(self, event: dict[str, Any]) -> None:
        await self._event_bus.publish(self._review_id, {**event, "review_id": self._review_id})

    def build_session_report(self, *, report_markdown: str) -> SessionReport:
        duration_ms = None
        if self._started_at_ms is not None and self._completed_at_ms is not None:
            duration_ms = self._completed_at_ms - self._started_at_ms
        return SessionReport(
            agent_id=self._agent_id,
            display_name=self._display_name,
            model=self._model,
            status=self._status,
            started_at=self._started_at_ms,
            completed_at=self._completed_at_ms,
            duration_ms=duration_ms,
            report_markdown=report_markdown,
            metrics=SessionMetrics.model_validate(self._metrics),
            tool_call_count=self._tool_call_count,
        )


async def _publish(event_bus: EventBus, review_id: str, event: dict[str, Any]) -> None:
    await event_bus.publish(review_id, {**event, "review_id": review_id})


def _is_text_candidate(path: Path) -> bool:
    supported, _ = is_supported_text_file(path)
    return supported


def _iter_repo_files(root: Path) -> list[str]:
    files: list[str] = []
    skip_dirs = {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "dist",
        "build",
        "coverage",
        ".pytest_cache",
    }
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if _is_text_candidate(path):
            files.append(rel)
    return sorted(files)


def _contains_any(path: str, needles: tuple[str, ...]) -> bool:
    normalized = path.lower()
    return any(needle in normalized for needle in needles)


def _pick_first(paths: list[str], limit: int) -> list[str]:
    return paths[:limit]


def _dedupe(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def _select_candidates(files: list[str], needles: tuple[str, ...], limit: int) -> list[str]:
    return _pick_first([path for path in files if _contains_any(path, needles)], limit)


def build_llm_review_plan(root: str, focus_prompt: str) -> LLMReviewPlan:
    root_path = Path(root)
    files = _iter_repo_files(root_path)

    docs = _select_candidates(files, ("spec", "adr", "readme", "docs/"), 6)
    manifests = _select_candidates(
        files,
        ("pyproject.toml", "package.json", "requirements", "uv.lock", "package-lock.json"),
        6,
    )
    ci = _select_candidates(files, (".github/workflows", "docker", "compose", "ci"), 4)
    configs = _select_candidates(files, ("config", ".env", "settings", "vite.config"), 4)
    schemas = _select_candidates(files, ("schema", "migration", "openapi", "api_spec"), 4)
    entrypoints = _select_candidates(files, ("main.py", "app.py", "server", "index.", "main."), 6)
    tests = _select_candidates(files, ("test", "__tests__", "spec."), 6)
    core_code = _pick_first(
        [
            path
            for path in files
            if path.startswith(("src/", "app/", "backend/", "frontend/"))
            and not _contains_any(path, ("test", "docs/"))
        ],
        8,
    )

    shared_core_files = _dedupe(docs + manifests[:2] + entrypoints[:2] + core_code[:4] + tests[:2])[
        :12
    ]
    artifact_files = _dedupe(docs + manifests + ci + configs + schemas + tests)[:16]

    risk_hypotheses = [
        "Generated code may drift from docs/spec without updating tests or runtime configuration.",
        "Abstractions may overfit imagined future states rather than present requirements.",
    ]
    if any("auth" in path.lower() for path in files):
        risk_hypotheses.append(
            "Security-critical boundaries may look complete while lacking enforcement."
        )
    if tests:
        risk_hypotheses.append(
            "Tests may mirror implementation rather than verify user-visible behavior."
        )
    if not ci:
        risk_hypotheses.append(
            "Build/runtime assumptions may be undocumented because CI evidence is missing."
        )

    assignments: list[SpecialistAssignment] = []
    for role, display_name in _ROLE_DISPLAY_NAMES.items():
        if role in {"challenger", "judge"}:
            continue
        extras = _select_candidates(files, _SPECIALIST_PATTERNS[role], 6)
        extras = [path for path in extras if path not in shared_core_files]
        assignments.append(
            SpecialistAssignment(
                agent_id=role,
                role=role,
                display_name=display_name,
                shared_core_files=shared_core_files,
                artifact_files=artifact_files,
                role_extra_files=extras[:4],
                focus=f"{focus_prompt}\n\nStrict audit lens: {_ROLE_FOCUS[role]}",
                risk_hypotheses=risk_hypotheses[:4],
            )
        )

    return LLMReviewPlan(
        shared_core_files=shared_core_files,
        artifact_files=artifact_files,
        assignments=assignments,
        risk_hypotheses=risk_hypotheses,
    )


def _verification_slice(role: str, summary: VerificationSummary) -> list[dict[str, Any]]:
    wanted = {
        "spec_drift": {"tests", "build"},
        "architecture_integrity": {"lint", "typecheck", "build"},
        "security_boundary": {"security_scan", "lint"},
        "runtime_operational": {"tests", "build", "typecheck", "lint", "security_scan"},
        "test_integrity": {"tests", "db_coverage", "e2e_coverage", "integration_label_fidelity"},
        "llm_artifact_simplification": {"lint", "build"},
    }.get(role, set())
    checks = [
        check.model_dump()
        for check in summary.checks
        if check.name in wanted
        or (
            role == "test_integrity"
            and check.kind_hint in {FindingKind.COVERAGE_GAP, FindingKind.LABEL_MISMATCH}
        )
    ]
    return checks if checks else [check.model_dump() for check in summary.checks[:3]]


def _specialist_system_prompt(role: str) -> str:
    return f"""You are a strict code review specialist for fully LLM-generated repositories.

You must review only through the provided tools and then call
submit_findings with structured output.

Behavioral rules:
- Be skeptical of elegant-looking abstractions that lack evidence.
- Treat missing runtime evidence as signal, not noise.
- Prefer precise, evidence-backed findings over broad commentary.
- If you find no issues in an audited dimension, record that in no_issue_sections.
- Do not invent files, APIs, or product requirements.
- All outward-facing output, including summaries and rationale,
  must be written in Traditional Chinese.

Your audit lens:
{_ROLE_FOCUS[role]}
"""


def _specialist_prompt(
    assignment: SpecialistAssignment, verification_slice: list[dict[str, Any]]
) -> str:
    shared = "\n".join(f"- {path}" for path in assignment.shared_core_files) or "- none"
    artifacts = "\n".join(f"- {path}" for path in assignment.artifact_files[:8]) or "- none"
    extras = "\n".join(f"- {path}" for path in assignment.role_extra_files) or "- none"
    verification_json = json.dumps(verification_slice, ensure_ascii=False, indent=2)
    return (
        f"Review agent: {assignment.display_name}\n"
        f"Focus:\n{assignment.focus}\n\n"
        f"Risk hypotheses:\n"
        + "\n".join(f"- {item}" for item in assignment.risk_hypotheses)
        + "\n\n"
        f"Shared core files:\n{shared}\n\n"
        f"Artifact files:\n{artifacts}\n\n"
        f"Role-specific extra files:\n{extras}\n\n"
        f"Verification slice:\n```json\n{verification_json}\n```\n\n"
        "所有最終輸出與工具欄位文字都必須使用繁體中文。\n\n"
        "Workflow:\n"
        "1. Read the shared core files relevant to your lens.\n"
        "2. Read your role-specific extra files.\n"
        "3. Pull in at most 3 dependency/context files only if needed for a concrete claim.\n"
        "4. Call submit_findings with structured findings.\n"
        "5. If nothing is worth raising, submit empty findings and explicit no_issue_sections.\n"
    )


def _fallback_finding_submission(assignment: SpecialistAssignment) -> FindingSubmission:
    return FindingSubmission(
        agent_id=assignment.agent_id,
        audited_dimensions=[assignment.role],
        no_issue_sections=[f"{assignment.display_name} 沒有提交結構化 findings。"],
        findings=[],
        rationale_markdown="此 session 未提交結構化 findings，請將其視為低信心結果。",
    )


def _verification_findings(summary: VerificationSummary) -> list[Finding]:
    findings: list[Finding] = []
    for check in summary.checks:
        if check.status == "passed":
            continue
        label = verification_check_title(check)
        kind = check.kind_hint
        if kind == FindingKind.RUNTIME_FAILURE:
            is_canonical_blocker = (
                check.blocking
                and check.status == "failed"
                and (
                    check.role == VerificationRole.CANONICAL
                    or label in summary.blocking_failures
                    or check.name in summary.blocking_failures
                )
            )
            if check.status == "skipped":
                continue
            if is_canonical_blocker:
                severity = FindingSeverity.BLOCKING
                gate_impact = "正式 deterministic validation set 中存在阻擋失敗。"
            elif check.role == VerificationRole.SUPPLEMENTAL and check.status == "failed":
                severity = FindingSeverity.MAJOR
                gate_impact = "補充檢查失敗，但尚未證明它屬於正式 gate。"
            elif (
                check.status == "unavailable"
                and check.applicability == VerificationApplicability.REQUIRED
            ):
                severity = FindingSeverity.MAJOR
                gate_impact = "必要的 deterministic validation 證據不可用，需要人工確認。"
            else:
                severity = FindingSeverity.MINOR
                gate_impact = "目前僅能視為補充或探索性訊號。"
            why_it_matters = (
                "在嚴格阻擋模式下，正式 gate 的執行失敗不能被忽略；"
                "非 canonical 失敗則必須先確認其適用性。"
            )
            suggested_fix = (
                f"確認 `{label}` 的來源、適用範圍與工作目錄，"
                "再決定它應屬於 canonical gate、supplemental check，或 stale scaffold。"
            )
        elif kind == FindingKind.COVERAGE_GAP:
            severity = (
                FindingSeverity.MAJOR
                if check.scope in {"db", "e2e", "repo-wide"}
                else FindingSeverity.MINOR
            )
            gate_impact = (
                "沒有 canonical blocking failure，但預設驗證 gate 對關鍵路徑只提供部分覆蓋。"
            )
            why_it_matters = "測試綠燈不代表關鍵 DB / integration 路徑已被預設 gate 驗證。"
            suggested_fix = (
                f"將 `{label}` 明確標成 opt-in coverage，或補上可在預設 gate 中執行的代表性驗證。"
            )
        elif kind == FindingKind.LABEL_MISMATCH:
            severity = (
                FindingSeverity.MAJOR
                if check.role != VerificationRole.STALE_SUSPECT
                else FindingSeverity.MINOR
            )
            gate_impact = "測試名稱與實際測試層級不一致，容易讓團隊高估覆蓋範圍。"
            why_it_matters = (
                "integration 標籤若主要靠內部 mock 支撐，"
                "會讓團隊誤把 controller/API-level 測試當成跨邊界驗證。"
            )
            suggested_fix = f"重新命名 `{label}`，或補上真正不 patch 關鍵邊界的系統層驗證。"
        else:
            if check.status != "unavailable":
                continue
            severity = FindingSeverity.MINOR
            gate_impact = "目前缺少足夠的 deterministic validation discovery 訊號。"
            why_it_matters = (
                "沒有可安全執行的驗證入口時，reviewer 應明確標示證據不足，"
                "而不是假設 repo 已全數通過。"
            )
            suggested_fix = (
                "補充 CI、task runner、或 docs 中的正式驗證入口，"
                "讓 reviewer 能穩定發現 canonical checks。"
            )
        findings.append(
            Finding(
                id=f"verification::{check.name}::{check.scope}::{check.working_dir}",
                category=kind.value,
                severity=severity,
                drift_type=DriftType.RUNTIME,
                kind=kind,
                summary=f"{label}：{verification_status_label(check)}。{check.summary}",
                claim=check.summary,
                evidence_refs=[EvidenceRef(kind="runtime", label=label)],
                confidence=min(
                    1.0, check.confidence + (0.15 if check.status == "failed" else 0.05)
                ),
                why_it_matters=why_it_matters,
                suggested_fix=suggested_fix,
                gate_impact=gate_impact,
                cluster_key=f"verification::{check.name}::{check.scope}::{check.working_dir}",
                assumption=(
                    "此結論以 live repo / manifests / docs / executed checks 為優先；"
                    "若 repo 另有未發現的 canonical gate，"
                    "需重新分類。"
                ),
                affected_scope=check.scope,
                agent_id="verification",
                rationale_markdown=check.output_excerpt,
            )
        )
    return findings


def _severity_score(severity: FindingSeverity) -> int:
    return {
        FindingSeverity.SUGGESTION: 0,
        FindingSeverity.MINOR: 1,
        FindingSeverity.MAJOR: 2,
        FindingSeverity.BLOCKING: 3,
    }[severity]


def _cluster_findings(findings: list[Finding]) -> dict[str, list[Finding]]:
    clusters: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        clusters[finding.cluster_key].append(finding)
    return dict(clusters)


def _cluster_event_payload(cluster_key: str, findings: list[Finding]) -> dict[str, Any]:
    severities = sorted({_severity_score(item.severity) for item in findings}, reverse=True)
    return {
        "type": "finding.clustered",
        "cluster_key": cluster_key,
        "count": len(findings),
        "severity_span": max(severities) - min(severities) if severities else 0,
        "deterministic_evidence": any(
            ref.kind == "runtime" for finding in findings for ref in finding.evidence_refs
        ),
    }


def _candidate_cluster_score(cluster_findings: list[Finding]) -> tuple[int, int]:
    highest = max(_severity_score(item.severity) for item in cluster_findings)
    disagreements = len({_severity_score(item.severity) for item in cluster_findings})
    return highest, disagreements


def _challenge_candidates(
    findings: list[Finding],
    verification_summary: VerificationSummary,
) -> list[list[Finding]]:
    candidates: list[list[Finding]] = []
    clusters = _cluster_findings(findings)
    for cluster_findings in clusters.values():
        severity_scores = {_severity_score(item.severity) for item in cluster_findings}
        evidence_count = sum(len(item.evidence_refs) for item in cluster_findings)
        has_runtime_gap = bool(
            verification_summary.unavailable_required or verification_summary.blocking_failures
        )
        highest = max(severity_scores)
        if highest == _severity_score(FindingSeverity.BLOCKING) and evidence_count < 2:
            candidates.append(cluster_findings)
            continue
        if severity_scores and max(severity_scores) - min(severity_scores) >= 2:
            candidates.append(cluster_findings)
            continue
        if has_runtime_gap and any(
            item.drift_type == DriftType.RUNTIME for item in cluster_findings
        ):
            candidates.append(cluster_findings)

    candidates.sort(key=_candidate_cluster_score, reverse=True)
    return candidates[:8]


def _apply_challenge_decisions(
    findings: list[Finding],
    decisions: list[ChallengeDecision],
) -> tuple[list[Finding], list[Finding]]:
    by_cluster = _cluster_findings(findings)
    rejected: list[Finding] = []
    retained: list[Finding] = []
    decision_map = {decision.cluster_key: decision for decision in decisions}

    for cluster_key, cluster_findings in by_cluster.items():
        decision = decision_map.get(cluster_key)
        if decision is None:
            retained.extend(cluster_findings)
            continue

        if decision.disposition == "reject":
            rejected.extend(cluster_findings)
            continue

        if decision.disposition == "downgrade" and decision.recommended_severity is not None:
            for finding in cluster_findings:
                retained.append(
                    finding.model_copy(
                        update={
                            "severity": decision.recommended_severity,
                            "rationale_markdown": decision.reason,
                        }
                    )
                )
            continue

        retained.extend(cluster_findings)

    return retained, rejected


def _derive_drift_summary(findings: list[Finding]) -> DriftSummary:
    counts = Counter(
        item.drift_type.value for item in findings if item.drift_type != DriftType.NONE
    )
    top = [name for name, _ in counts.most_common(3)]
    if not top:
        return DriftSummary(top_drift_types=[], summary="未識別出明顯的漂移主題。")
    return DriftSummary(
        top_drift_types=top,
        summary="主要漂移型態：" + "、".join(top),
    )


def _compute_convergence_metrics(
    findings: list[Finding],
    disputed: list[Finding],
    challenged_clusters: int,
) -> ConvergenceMetrics:
    clusters = _cluster_findings(findings)
    agreement_count = sum(1 for items in clusters.values() if len(items) > 1)
    disagreement_count = sum(
        1
        for items in clusters.values()
        if len({_severity_score(item.severity) for item in items}) > 1
    )
    evidence_density = (
        sum(len(item.evidence_refs) for item in findings) / max(len(findings), 1)
        if findings
        else 0.0
    )
    deterministic = sum(
        1
        for items in clusters.values()
        if any(ref.kind == "runtime" for item in items for ref in item.evidence_refs)
    )
    return ConvergenceMetrics(
        agreement_count=agreement_count,
        disagreement_count=disagreement_count,
        evidence_density=round(evidence_density, 2),
        deterministic_evidence_presence=round(deterministic / max(len(clusters), 1), 2)
        if clusters
        else 0.0,
        unresolved_dispute_count=len(_cluster_findings(disputed)),
        challenged_cluster_count=challenged_clusters,
    )


def _deterministic_verdict(
    findings: list[Finding],
    disputed_findings: list[Finding],
    verification_summary: VerificationSummary,
) -> GateVerdict:
    if any(item.severity == FindingSeverity.BLOCKING for item in findings):
        return GateVerdict.FAIL
    if verification_summary.blocking_failures:
        return GateVerdict.FAIL
    if any(
        item.severity == FindingSeverity.MAJOR
        and item.kind in {FindingKind.COVERAGE_GAP, FindingKind.LABEL_MISMATCH, FindingKind.ENV_GAP}
        for item in findings
    ):
        return GateVerdict.NEEDS_HUMAN_REVIEW
    if verification_summary.unavailable_required or any(
        item.severity == FindingSeverity.MAJOR for item in disputed_findings
    ):
        return GateVerdict.NEEDS_HUMAN_REVIEW
    return GateVerdict.PASS


def _build_report(
    *,
    verdict: GateVerdict,
    findings: list[Finding],
    disputed_findings: list[Finding],
    rejected_findings: list[Finding],
    verification_summary: VerificationSummary,
    drift_summary: DriftSummary,
) -> str:
    lines = [
        "# LLM Repo 嚴格審查報告",
        "",
        f"**最終裁決：** {verdict.value}",
        "",
        f"**裁決依據：** {verification_summary.verdict_predicate or '未提供'}",
        "",
        f"**漂移摘要：** {drift_summary.summary}",
        "",
        "## Deterministic Verification",
    ]
    if verification_summary.checks:
        for check in verification_summary.checks:
            lines.append(
                f"- `{verification_check_title(check)}`："
                f"{verification_status_label(check)}，{check.summary}"
            )
    else:
        lines.append("- 本次沒有額外執行 runtime checks。")

    def emit(section: str, items: list[Finding]) -> None:
        if not items:
            return
        lines.extend(["", f"## {section}"])
        for item in items:
            evidence = (
                ", ".join(ref.label or ref.path or ref.kind for ref in item.evidence_refs[:3])
                or "未提供證據"
            )
            lines.extend(
                [
                    f"- **{item.severity.value.upper()}** `{item.category}`：{item.summary}",
                    f"  - 證據：{evidence}",
                    f"  - 影響：{item.why_it_matters}",
                    f"  - 建議修正：{item.suggested_fix}",
                ]
            )

    emit("共識問題", findings)
    emit("爭議問題", disputed_findings)
    emit("被駁回的問題", rejected_findings)
    return "\n".join(lines).strip() + "\n"


async def _run_specialist(
    *,
    review_id: str,
    root: str,
    assignment: SpecialistAssignment,
    verification_summary: VerificationSummary,
    event_bus: EventBus,
    session_manager: SessionManager,
    model_router: ModelRouter,
) -> SpecialistRunResult:
    capture: list[FindingSubmission] = []

    async def submit_findings(invocation: ToolInvocation) -> ToolResult:
        try:
            submission = FindingSubmission.model_validate(invocation.arguments)
            normalized_findings = [
                finding.model_copy(update={"agent_id": assignment.agent_id})
                for finding in submission.findings
            ]
            normalized = submission.model_copy(update={"findings": normalized_findings})
            capture.append(normalized)
            for finding in normalized.findings:
                await _publish(
                    event_bus,
                    review_id,
                    {
                        "type": "finding.emitted",
                        "agent": assignment.agent_id,
                        "display_name": assignment.display_name,
                        "finding": finding.model_dump(),
                    },
                )
            return ToolResult(text_result_for_llm="已接受 findings。", result_type="success")
        except Exception as exc:
            return ToolResult(
                text_result_for_llm=f"findings 格式無效：{exc}", result_type="failure"
            )

    tools = [
        *build_codebase_tools(root, start_time=time.monotonic()),
        Tool(
            name="submit_findings",
            description="Submit structured findings for this strict review lens.",
            parameters=_inline_schema_refs(FindingSubmission.model_json_schema()),
            handler=submit_findings,
        ),
    ]
    model_role = _ROLE_MODELS[assignment.role]
    model = model_router.get_model(model_role)
    session = await session_manager.create_session(
        SessionConfig(
            model=model,
            tools=tools,
            system_message={
                "mode": "replace",
                "content": _specialist_system_prompt(assignment.role),
            },
            streaming=True,
            working_directory=root,
        )
    )
    runner = StrictSessionAgent(
        session=session,
        event_bus=event_bus,
        review_id=review_id,
        agent_id=assignment.agent_id,
        display_name=assignment.display_name,
        base_role=assignment.role,
        model=model,
        timeout_s=_PRIMARY_TIMEOUT_S,
    )
    verification_slice = _verification_slice(assignment.role, verification_summary)
    raw_output = await runner.run(_specialist_prompt(assignment, verification_slice))
    submission = capture[0] if capture else _fallback_finding_submission(assignment)
    session_report = runner.build_session_report(
        report_markdown=build_strict_session_report(
            agent_id=assignment.agent_id,
            display_name=assignment.display_name,
            model=model,
            status=runner._status,
            started_at=runner._started_at_ms,
            completed_at=runner._completed_at_ms,
            duration_ms=(
                runner._completed_at_ms - runner._started_at_ms
                if runner._started_at_ms is not None and runner._completed_at_ms is not None
                else None
            ),
            metrics=SessionMetrics.model_validate(runner._metrics),
            tool_call_count=runner._tool_call_count,
            raw_output=raw_output,
            findings=submission.findings,
            no_issue_sections=submission.no_issue_sections,
            audited_dimensions=submission.audited_dimensions or [assignment.role],
            rationale_markdown=submission.rationale_markdown,
            verification_checks=[
                VerificationCheckResult.model_validate(item) for item in verification_slice
            ],
        ).report_markdown
        or ""
    ).model_copy(
        update={
            "findings": submission.findings,
            "no_issue_sections": submission.no_issue_sections,
            "audited_dimensions": submission.audited_dimensions or [assignment.role],
            "rationale_markdown": submission.rationale_markdown,
            "verification_checks": [
                VerificationCheckResult.model_validate(item) for item in verification_slice
            ],
        }
    )
    return SpecialistRunResult(submission=submission, session_report=session_report)


def _challenge_prompt(
    candidates: list[list[Finding]], verification_summary: VerificationSummary
) -> str:
    payload = {
        "clusters": [[finding.model_dump() for finding in cluster] for cluster in candidates],
        "verification_summary": verification_summary.model_dump(),
    }
    return (
        "你是嚴格模式的挑戰者。你的工作是推翻或降級證據薄弱的 findings，而不是新增問題。\n\n"
        "所有輸出都必須使用繁體中文。檢查候選叢集後，呼叫 submit_challenge。\n\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
    )


async def _run_challenger(
    *,
    review_id: str,
    root: str,
    findings: list[Finding],
    verification_summary: VerificationSummary,
    event_bus: EventBus,
    session_manager: SessionManager,
    model_router: ModelRouter,
) -> ChallengeRunResult:
    candidates = _challenge_candidates(findings, verification_summary)
    if not candidates:
        session_report = SessionReport(
            agent_id="challenger",
            display_name=role_display_name("challenger"),
            model=None,
            status="complete",
            report_markdown="# 挑戰者 Session 報告\n\n- 沒有需要 challenge 的 findings。\n",
            no_issue_sections=["沒有需要 challenge 的項目。"],
        )
        return ChallengeRunResult(decisions=[], session_report=session_report)

    capture: list[ChallengeSubmission] = []

    async def submit_challenge(invocation: ToolInvocation) -> ToolResult:
        try:
            submission = ChallengeSubmission.model_validate(invocation.arguments)
            capture.append(submission)
            for decision in submission.decisions:
                await _publish(
                    event_bus,
                    review_id,
                    {
                        "type": "finding.challenged",
                        "agent": "challenger",
                        "decision": decision.model_dump(),
                    },
                )
            return ToolResult(text_result_for_llm="已接受 challenge 結果。", result_type="success")
        except Exception as exc:
            return ToolResult(
                text_result_for_llm=f"challenge 格式無效：{exc}", result_type="failure"
            )

    tools = [
        *build_codebase_tools(root, start_time=time.monotonic()),
        Tool(
            name="submit_challenge",
            description="Submit dispositions for challenged finding clusters.",
            parameters=_inline_schema_refs(ChallengeSubmission.model_json_schema()),
            handler=submit_challenge,
        ),
    ]
    model = model_router.get_model(AgentRole.CHALLENGER)
    session = await session_manager.create_session(
        SessionConfig(
            model=model,
            tools=tools,
            system_message={
                "mode": "replace",
                "content": (
                    "You are the strict challenger. Reject findings that are weakly evidenced, "
                    "overstated, or contradicted by deterministic verification. "
                    "All outward-facing output must be written in Traditional Chinese."
                ),
            },
            streaming=True,
            working_directory=root,
        )
    )
    runner = StrictSessionAgent(
        session=session,
        event_bus=event_bus,
        review_id=review_id,
        agent_id="challenger",
        display_name=_ROLE_DISPLAY_NAMES["challenger"],
        base_role="challenger",
        model=model,
        timeout_s=_CHALLENGER_TIMEOUT_S,
    )
    raw_output = await runner.run(_challenge_prompt(candidates, verification_summary))
    submission = capture[0] if capture else ChallengeSubmission(decisions=[], rationale_markdown="")
    challenge_notes = [
        f"{decision.cluster_key}：{decision.disposition}（{decision.reason}）"
        for decision in submission.decisions
    ]
    session_report = runner.build_session_report(
        report_markdown=build_challenger_session_report(
            agent_id="challenger",
            display_name=_ROLE_DISPLAY_NAMES["challenger"],
            model=model,
            status=runner._status,
            started_at=runner._started_at_ms,
            completed_at=runner._completed_at_ms,
            duration_ms=(
                runner._completed_at_ms - runner._started_at_ms
                if runner._started_at_ms is not None and runner._completed_at_ms is not None
                else None
            ),
            metrics=SessionMetrics.model_validate(runner._metrics),
            tool_call_count=runner._tool_call_count,
            raw_output=raw_output,
            challenge_notes=challenge_notes,
            rationale_markdown=submission.rationale_markdown,
        ).report_markdown
        or ""
    ).model_copy(
        update={
            "no_issue_sections": (
                ["已完成 challenge 流程。"] if challenge_notes else ["沒有需要 challenge 的項目。"]
            ),
            "rationale_markdown": submission.rationale_markdown,
        }
    )
    return ChallengeRunResult(decisions=submission.decisions, session_report=session_report)


def _judge_prompt(
    findings: list[Finding],
    rejected_findings: list[Finding],
    verification_summary: VerificationSummary,
    challenged_cluster_count: int,
) -> str:
    payload = {
        "findings": [finding.model_dump() for finding in findings],
        "rejected_findings": [finding.model_dump() for finding in rejected_findings],
        "verification_summary": verification_summary.model_dump(),
        "challenged_cluster_count": challenged_cluster_count,
    }
    return (
        "你是嚴格模式的最終裁決者。請將 findings 分群、計算收斂情況、決定最終 verdict，"
        "並呼叫 submit_judgment。所有輸出都必須使用繁體中文。\n\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
    )


def _deterministic_judgment(
    findings: list[Finding],
    rejected_findings: list[Finding],
    verification_summary: VerificationSummary,
    challenged_cluster_count: int,
) -> JudgmentSubmission:
    clusters = _cluster_findings(findings)
    disputed: list[Finding] = []
    consensus: list[Finding] = []
    for cluster_findings in clusters.values():
        if len({_severity_score(item.severity) for item in cluster_findings}) > 1:
            disputed.extend(cluster_findings)
        else:
            consensus.append(cluster_findings[0])

    drift_summary = _derive_drift_summary(findings)
    convergence = _compute_convergence_metrics(findings, disputed, challenged_cluster_count)
    verdict = _deterministic_verdict(consensus, disputed, verification_summary)
    report = _build_report(
        verdict=verdict,
        findings=consensus,
        disputed_findings=disputed,
        rejected_findings=rejected_findings,
        verification_summary=verification_summary,
        drift_summary=drift_summary,
    )
    return JudgmentSubmission(
        consensus_findings=consensus,
        disputed_findings=disputed,
        rejected_findings=rejected_findings,
        drift_summary=drift_summary,
        convergence_metrics=convergence,
        verdict=verdict,
        final_report=report,
    )


async def _run_judge(
    *,
    review_id: str,
    findings: list[Finding],
    rejected_findings: list[Finding],
    verification_summary: VerificationSummary,
    challenged_cluster_count: int,
    event_bus: EventBus,
    session_manager: SessionManager,
    model_router: ModelRouter,
) -> JudgeRunResult:
    capture: list[JudgmentSubmission] = []

    async def submit_judgment(invocation: ToolInvocation) -> ToolResult:
        try:
            submission = JudgmentSubmission.model_validate(invocation.arguments)
            capture.append(submission)
            await _publish(
                event_bus,
                review_id,
                {
                    "type": "judge.summary",
                    "agent": "judge",
                    "summary": submission.model_dump(),
                },
            )
            await _publish(
                event_bus,
                review_id,
                {
                    "type": "review.verdict",
                    "verdict": submission.verdict.value,
                },
            )
            return ToolResult(text_result_for_llm="已接受最終裁決。", result_type="success")
        except Exception as exc:
            return ToolResult(
                text_result_for_llm=f"judgment 格式無效：{exc}", result_type="failure"
            )

    model = model_router.get_model(AgentRole.JUDGE)
    session = await session_manager.create_session(
        SessionConfig(
            model=model,
            tools=[
                Tool(
                    name="submit_judgment",
                    description="Submit the final strict-review judgment and report.",
                    parameters=_inline_schema_refs(JudgmentSubmission.model_json_schema()),
                    handler=submit_judgment,
                )
            ],
            system_message={
                "mode": "replace",
                "content": (
                    "You are the strict judge for a fully LLM-generated repo review. Use the "
                    "structured findings, verification evidence, and challenger results to produce "
                    "a blocking verdict and a concise report. All outward-facing output must be "
                    "written in Traditional Chinese."
                ),
            },
            streaming=True,
        )
    )
    runner = StrictSessionAgent(
        session=session,
        event_bus=event_bus,
        review_id=review_id,
        agent_id="judge",
        display_name=_ROLE_DISPLAY_NAMES["judge"],
        base_role="judge",
        model=model,
        timeout_s=_JUDGE_TIMEOUT_S,
    )
    raw_output = await runner.run(
        _judge_prompt(findings, rejected_findings, verification_summary, challenged_cluster_count)
    )
    if capture:
        submission = capture[0]
        session_report = runner.build_session_report(
            report_markdown=build_judge_session_report(
                agent_id="judge",
                display_name=_ROLE_DISPLAY_NAMES["judge"],
                model=model,
                status=runner._status,
                started_at=runner._started_at_ms,
                completed_at=runner._completed_at_ms,
                duration_ms=(
                    runner._completed_at_ms - runner._started_at_ms
                    if runner._started_at_ms is not None and runner._completed_at_ms is not None
                    else None
                ),
                metrics=SessionMetrics.model_validate(runner._metrics),
                tool_call_count=runner._tool_call_count,
                raw_output=raw_output,
                verdict=submission.verdict,
                consensus_findings=submission.consensus_findings,
                disputed_findings=submission.disputed_findings,
                rationale_markdown=submission.final_report,
            ).report_markdown
            or ""
        ).model_copy(
            update={
                "findings": [*submission.consensus_findings, *submission.disputed_findings],
                "no_issue_sections": [f"已完成最終裁決：{submission.verdict.value}。"],
                "rationale_markdown": submission.final_report,
            }
        )
        return JudgeRunResult(submission=submission, session_report=session_report)

    fallback = _deterministic_judgment(
        findings,
        rejected_findings,
        verification_summary,
        challenged_cluster_count,
    )
    await _publish(
        event_bus,
        review_id,
        {
            "type": "judge.summary",
            "agent": "judge",
            "summary": fallback.model_dump(),
        },
    )
    await _publish(
        event_bus,
        review_id,
        {
            "type": "review.verdict",
            "verdict": fallback.verdict.value,
        },
    )
    session_report = SessionReport(
        agent_id="judge",
        display_name=_ROLE_DISPLAY_NAMES["judge"],
        model=model,
        status="complete",
        report_markdown=build_judge_session_report(
            agent_id="judge",
            display_name=_ROLE_DISPLAY_NAMES["judge"],
            model=model,
            status="complete",
            started_at=None,
            completed_at=None,
            duration_ms=None,
            metrics=SessionMetrics(),
            tool_call_count=0,
            raw_output=raw_output,
            verdict=fallback.verdict,
            consensus_findings=fallback.consensus_findings,
            disputed_findings=fallback.disputed_findings,
            rationale_markdown=fallback.final_report,
        ).report_markdown,
        findings=[*fallback.consensus_findings, *fallback.disputed_findings],
        no_issue_sections=[f"已完成最終裁決：{fallback.verdict.value}。"],
        rationale_markdown=fallback.final_report,
    )
    return JudgeRunResult(submission=fallback, session_report=session_report)


async def run_llm_repo_pipeline(
    *,
    review_id: str,
    request: Any,
    event_bus: EventBus,
    session_manager: SessionManager,
    model_router: ModelRouter,
    review_store: ReviewStore | None = None,
) -> StrictReviewOutcome:
    """
    Execute the strict LLM-native review pipeline and return a structured outcome.
    """
    root_path = Path(request.review_root)
    budget_manager = BudgetManager()
    plan = build_llm_review_plan(request.review_root, request.focus_prompt)
    await _publish(
        event_bus,
        review_id,
        {
            "type": "orchestrator.plan",
            "plan": plan.model_dump(),
        },
    )

    await _publish(event_bus, review_id, {"type": "verification.started"})
    verification_summary = await run_verification(
        root_path,
        request.evidence_mode,
        selected_paths=request.selected_paths,
    )
    await _publish(
        event_bus,
        review_id,
        {
            "type": "verification.completed",
            "verification_summary": verification_summary.model_dump(),
        },
    )

    assignments: list[SpecialistAssignment] = []
    for assignment in plan.assignments:
        sharded = budget_manager.shard_assignment(
            root_path,
            assignment,
            _verification_slice(assignment.role, verification_summary),
        )
        assignments.extend(sharded)

    specialist_results = await asyncio.gather(
        *[
            _run_specialist(
                review_id=review_id,
                root=request.review_root,
                assignment=assignment,
                verification_summary=verification_summary,
                event_bus=event_bus,
                session_manager=session_manager,
                model_router=model_router,
            )
            for assignment in assignments
        ]
    )

    findings = _verification_findings(verification_summary)
    session_reports = [result.session_report for result in specialist_results]
    for result in specialist_results:
        findings.extend(result.submission.findings)

    for cluster_key, cluster_findings in _cluster_findings(findings).items():
        await _publish(event_bus, review_id, _cluster_event_payload(cluster_key, cluster_findings))

    rejected_findings: list[Finding] = []
    if request.convergence_mode.value in {"adaptive_rerun", "fixed_double_pass"} and findings:
        challenge_result = await _run_challenger(
            review_id=review_id,
            root=request.review_root,
            findings=findings,
            verification_summary=verification_summary,
            event_bus=event_bus,
            session_manager=session_manager,
            model_router=model_router,
        )
        session_reports.append(challenge_result.session_report)
        findings, rejected_findings = _apply_challenge_decisions(
            findings, challenge_result.decisions
        )
        challenged_cluster_count = len(challenge_result.decisions)
    else:
        challenged_cluster_count = 0

    judge_result = await _run_judge(
        review_id=review_id,
        findings=findings,
        rejected_findings=rejected_findings,
        verification_summary=verification_summary,
        challenged_cluster_count=challenged_cluster_count,
        event_bus=event_bus,
        session_manager=session_manager,
        model_router=model_router,
    )
    session_reports.append(judge_result.session_report)
    judgment = judge_result.submission

    final_summary_markdown = build_final_summary_markdown(
        review_profile=ReviewProfile.LLM_REPO,
        final_report=judgment.final_report,
        session_reports=session_reports,
        verdict=judgment.verdict,
        verification_summary=verification_summary,
        convergence_metrics=judgment.convergence_metrics,
        drift_summary=judgment.drift_summary,
        consensus_findings=judgment.consensus_findings,
        disputed_findings=judgment.disputed_findings,
    )
    next_steps_markdown = build_next_steps_markdown(
        review_profile=ReviewProfile.LLM_REPO,
        session_reports=session_reports,
        verdict=judgment.verdict,
        verification_summary=verification_summary,
        consensus_findings=judgment.consensus_findings,
        disputed_findings=judgment.disputed_findings,
    )
    artifact_summary = build_artifact_summary(
        session_reports,
        final_summary_markdown,
        next_steps_markdown,
    )

    outcome = StrictReviewOutcome(
        report=judgment.final_report,
        verdict=judgment.verdict,
        findings=findings,
        consensus_findings=judgment.consensus_findings,
        disputed_findings=judgment.disputed_findings,
        convergence_metrics=judgment.convergence_metrics,
        verification_summary=verification_summary,
        drift_summary=judgment.drift_summary,
        session_reports=session_reports,
        final_summary_markdown=final_summary_markdown,
        next_steps_markdown=next_steps_markdown,
        artifact_summary=artifact_summary,
    )
    if review_store is not None:
        state = review_store.get(review_id)
        if state is not None:
            state.verdict = outcome.verdict
            state.findings = outcome.findings
            state.consensus_findings = outcome.consensus_findings
            state.disputed_findings = outcome.disputed_findings
            state.convergence_metrics = outcome.convergence_metrics
            state.verification_summary = outcome.verification_summary
            state.drift_summary = outcome.drift_summary
            state.session_reports = outcome.session_reports
            state.final_summary_markdown = outcome.final_summary_markdown
            state.next_steps_markdown = outcome.next_steps_markdown
            state.artifact_summary = outcome.artifact_summary

    return outcome
