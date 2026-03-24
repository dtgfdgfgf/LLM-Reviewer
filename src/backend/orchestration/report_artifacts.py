"""
Report artifact models and deterministic markdown builders.

These helpers keep review outputs portable across the API, the in-memory store,
and the frontend download actions.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from pydantic import BaseModel, Field

from backend.orchestration.strict_types import (
    ConvergenceMetrics,
    DriftSummary,
    Finding,
    FindingKind,
    FindingSeverity,
    GateVerdict,
    ReviewProfile,
    VerificationApplicability,
    VerificationCheckResult,
    VerificationRole,
    VerificationSummary,
)

ROLE_DISPLAY_NAMES_ZH: dict[str, str] = {
    "orchestrator": "協調規劃",
    "reviewer_1": "架構審查",
    "reviewer_2": "後端審查",
    "reviewer_3": "前端與體驗審查",
    "synthesizer": "最終報告整合",
    "spec_drift": "規格漂移",
    "architecture_integrity": "架構完整性",
    "security_boundary": "安全邊界",
    "runtime_operational": "執行期與營運",
    "test_integrity": "測試完整性",
    "llm_artifact_simplification": "LLM 產物與簡化",
    "challenger": "挑戰者",
    "judge": "最終裁決",
}

_VERIFICATION_STATUS_LABELS = {
    "passed": "通過",
    "failed": "失敗",
    "unavailable": "不可用",
    "skipped": "未配置或不適用",
}

_SEVERITY_LABELS = {
    FindingSeverity.BLOCKING: "阻擋",
    FindingSeverity.MAJOR: "重大",
    FindingSeverity.MINOR: "次要",
    FindingSeverity.SUGGESTION: "建議",
}

_GENERAL_SECTION_ALIASES = {
    "重大問題": {"重大問題", "critical issues"},
    "重要問題": {"重要問題", "significant issues"},
    "建議": {"建議", "suggestions"},
    "優點": {"優點", "strengths"},
}


class SessionMetrics(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    turns: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class SessionReport(BaseModel):
    agent_id: str
    display_name: str
    model: str | None = None
    status: str = Field(default="complete", description="running | complete | error")
    started_at: int | None = None
    completed_at: int | None = None
    duration_ms: int | None = None
    report_markdown: str | None = None
    metrics: SessionMetrics = Field(default_factory=SessionMetrics)
    tool_call_count: int = 0
    findings: list[Finding] = Field(default_factory=list)
    no_issue_sections: list[str] = Field(default_factory=list)
    audited_dimensions: list[str] = Field(default_factory=list)
    rationale_markdown: str = ""
    verification_checks: list[VerificationCheckResult] = Field(default_factory=list)


class ArtifactSummary(BaseModel):
    session_report_count: int = 0
    completed_session_count: int = 0
    final_summary_available: bool = False
    next_steps_available: bool = False


def role_display_name(agent_id: str, fallback: str | None = None) -> str:
    return ROLE_DISPLAY_NAMES_ZH.get(agent_id, fallback or agent_id)


def compact_session_report(report: SessionReport) -> SessionReport:
    return report.model_copy(
        update={
            "report_markdown": None,
            "rationale_markdown": "",
        }
    )


def build_artifact_summary(
    session_reports: list[SessionReport],
    final_summary_markdown: str | None,
    next_steps_markdown: str | None,
) -> ArtifactSummary:
    return ArtifactSummary(
        session_report_count=len(session_reports),
        completed_session_count=sum(1 for item in session_reports if item.status == "complete"),
        final_summary_available=bool(final_summary_markdown),
        next_steps_available=bool(next_steps_markdown),
    )


def build_general_session_report(
    *,
    agent_id: str,
    display_name: str,
    model: str | None,
    status: str,
    started_at: int | None,
    completed_at: int | None,
    duration_ms: int | None,
    metrics: SessionMetrics,
    tool_call_count: int,
    raw_output: str,
) -> SessionReport:
    raw_output = raw_output.strip()
    lines = _session_header_lines(
        title=f"{display_name} Session 報告",
        agent_id=agent_id,
        display_name=display_name,
        model=model,
        status=status,
        duration_ms=duration_ms,
        metrics=metrics,
        tool_call_count=tool_call_count,
    )
    lines.extend(
        [
            "",
            "## 原始審查內容",
            raw_output or "此 session 沒有產出文字結果。",
        ]
    )
    return SessionReport(
        agent_id=agent_id,
        display_name=display_name,
        model=model,
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        report_markdown="\n".join(lines).strip() + "\n",
        metrics=metrics,
        tool_call_count=tool_call_count,
    )


def build_strict_session_report(
    *,
    agent_id: str,
    display_name: str,
    model: str | None,
    status: str,
    started_at: int | None,
    completed_at: int | None,
    duration_ms: int | None,
    metrics: SessionMetrics,
    tool_call_count: int,
    raw_output: str,
    findings: list[Finding],
    no_issue_sections: list[str],
    audited_dimensions: list[str],
    rationale_markdown: str,
    verification_checks: list[VerificationCheckResult] | None = None,
) -> SessionReport:
    verification_checks = verification_checks or []
    lines = _session_header_lines(
        title=f"{display_name} Session 報告",
        agent_id=agent_id,
        display_name=display_name,
        model=model,
        status=status,
        duration_ms=duration_ms,
        metrics=metrics,
        tool_call_count=tool_call_count,
    )
    lines.extend(
        [
            "",
            "## 審查維度",
            *(_bullet_lines(audited_dimensions) or ["- 未提供審查維度。"]),
            "",
            "## 有問題",
            *(_finding_lines(findings) or ["- 本 session 未提交結構化問題。"]),
            "",
            "## 表現良好",
            *(_bullet_lines(no_issue_sections) or ["- 本 session 未明確列出表現良好的項目。"]),
            "",
            "## 證據 / 驗證摘要",
            *(_verification_lines(verification_checks) or ["- 此 session 沒有額外的驗證切片。"]),
        ]
    )
    if rationale_markdown.strip():
        lines.extend(["", "## 補充判斷", rationale_markdown.strip()])
    lines.extend(
        ["", "## 原始 Session 輸出", raw_output.strip() or "此 session 沒有產出文字結果。"]
    )
    return SessionReport(
        agent_id=agent_id,
        display_name=display_name,
        model=model,
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        report_markdown="\n".join(lines).strip() + "\n",
        metrics=metrics,
        tool_call_count=tool_call_count,
        findings=findings,
        no_issue_sections=no_issue_sections,
        audited_dimensions=audited_dimensions,
        rationale_markdown=rationale_markdown,
        verification_checks=verification_checks,
    )


def build_challenger_session_report(
    *,
    agent_id: str,
    display_name: str,
    model: str | None,
    status: str,
    started_at: int | None,
    completed_at: int | None,
    duration_ms: int | None,
    metrics: SessionMetrics,
    tool_call_count: int,
    raw_output: str,
    challenge_notes: list[str],
    rationale_markdown: str,
) -> SessionReport:
    lines = _session_header_lines(
        title=f"{display_name} Session 報告",
        agent_id=agent_id,
        display_name=display_name,
        model=model,
        status=status,
        duration_ms=duration_ms,
        metrics=metrics,
        tool_call_count=tool_call_count,
    )
    lines.extend(
        [
            "",
            "## 挑戰結果",
            *(_bullet_lines(challenge_notes) or ["- 沒有需要挑戰的 findings。"]),
        ]
    )
    if rationale_markdown.strip():
        lines.extend(["", "## 補充判斷", rationale_markdown.strip()])
    lines.extend(
        ["", "## 原始 Session 輸出", raw_output.strip() or "此 session 沒有產出文字結果。"]
    )
    return SessionReport(
        agent_id=agent_id,
        display_name=display_name,
        model=model,
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        report_markdown="\n".join(lines).strip() + "\n",
        metrics=metrics,
        tool_call_count=tool_call_count,
        no_issue_sections=["已完成 challenge 流程。"]
        if challenge_notes
        else ["沒有需要 challenge 的項目。"],
        rationale_markdown=rationale_markdown,
    )


def build_judge_session_report(
    *,
    agent_id: str,
    display_name: str,
    model: str | None,
    status: str,
    started_at: int | None,
    completed_at: int | None,
    duration_ms: int | None,
    metrics: SessionMetrics,
    tool_call_count: int,
    raw_output: str,
    verdict: GateVerdict,
    consensus_findings: list[Finding],
    disputed_findings: list[Finding],
    rationale_markdown: str,
) -> SessionReport:
    lines = _session_header_lines(
        title=f"{display_name} Session 報告",
        agent_id=agent_id,
        display_name=display_name,
        model=model,
        status=status,
        duration_ms=duration_ms,
        metrics=metrics,
        tool_call_count=tool_call_count,
    )
    lines.extend(
        [
            "",
            "## 最終裁決",
            f"- 裁決：{verdict.value}",
            f"- 共識問題：{len(consensus_findings)} 項",
            f"- 爭議問題：{len(disputed_findings)} 項",
            "",
            "## 共識問題",
            *(_finding_lines(consensus_findings) or ["- 無。"]),
            "",
            "## 爭議問題",
            *(_finding_lines(disputed_findings) or ["- 無。"]),
        ]
    )
    if rationale_markdown.strip():
        lines.extend(["", "## 補充判斷", rationale_markdown.strip()])
    lines.extend(
        ["", "## 原始 Session 輸出", raw_output.strip() or "此 session 沒有產出文字結果。"]
    )
    return SessionReport(
        agent_id=agent_id,
        display_name=display_name,
        model=model,
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        report_markdown="\n".join(lines).strip() + "\n",
        metrics=metrics,
        tool_call_count=tool_call_count,
        findings=[*consensus_findings, *disputed_findings],
        no_issue_sections=[f"已完成最終裁決：{verdict.value}。"],
        rationale_markdown=rationale_markdown,
    )


def build_final_summary_markdown(
    *,
    review_profile: ReviewProfile,
    final_report: str,
    session_reports: list[SessionReport],
    verdict: GateVerdict | None = None,
    verification_summary: VerificationSummary | None = None,
    convergence_metrics: ConvergenceMetrics | None = None,
    drift_summary: DriftSummary | None = None,
    consensus_findings: list[Finding] | None = None,
    disputed_findings: list[Finding] | None = None,
) -> str:
    consensus_findings = consensus_findings or []
    disputed_findings = disputed_findings or []
    lines = [
        "# 最終統整文件",
        "",
        "## 最終判定",
        f"- Review 模式：{_profile_label(review_profile)}",
        f"- 最終裁決：{verdict.value if verdict else '未提供'}",
    ]
    if verification_summary and verification_summary.verdict_predicate:
        lines.append(f"- 裁決依據：{verification_summary.verdict_predicate}")
    if drift_summary and drift_summary.summary:
        lines.append(f"- 漂移摘要：{drift_summary.summary}")
    if final_report.strip():
        lines.extend(["", "## 綜合結論", _first_meaningful_paragraph(final_report)])

    lines.extend(["", "## 逐 Session 結果"])
    for report in session_reports:
        problems, strengths, evidence = _session_summary(report, review_profile)
        lines.extend(
            [
                "",
                f"### {report.display_name}",
                f"- 角色 ID：`{report.agent_id}`",
                f"- 模型：`{report.model or '未提供'}`",
                f"- 狀態：{report.status}",
                "",
                "#### 有問題",
                *(_bullet_lines(problems) or ["- 未列出明確問題。"]),
                "",
                "#### 表現良好",
                *(_bullet_lines(strengths) or ["- 未列出明確優點。"]),
                "",
                "#### 證據 / 驗證摘要",
                *(_bullet_lines(evidence) or ["- 無額外證據摘要。"]),
            ]
        )

    lines.extend(["", "## 驗證結果總覽"])
    if verification_summary and verification_summary.checks:
        lines.extend(_verification_lines(verification_summary.checks))
        if verification_summary.blocking_failures:
            lines.extend(
                ["", "### 阻擋失敗", *(_bullet_lines(verification_summary.blocking_failures))]
            )
        if verification_summary.unavailable_required:
            lines.extend(
                [
                    "",
                    "### 不可用或需人工確認",
                    *(_bullet_lines(verification_summary.unavailable_required)),
                ]
            )
    else:
        lines.append("- 本次沒有額外的 deterministic verification 結果。")

    lines.extend(["", "## 收斂情況"])
    if convergence_metrics:
        lines.extend(
            [
                f"- 一致項目：{convergence_metrics.agreement_count}",
                f"- 分歧項目：{convergence_metrics.disagreement_count}",
                (
                    "- 證據密度："
                    f"{convergence_metrics.evidence_density}（每個 finding 平均引用的證據數）"
                ),
                f"- 未解決爭議：{convergence_metrics.unresolved_dispute_count}",
                f"- 挑戰過的叢集：{convergence_metrics.challenged_cluster_count}",
            ]
        )
    else:
        lines.append("- 此模式未提供結構化收斂指標。")

    lines.extend(["", "## 仍待人工判斷或未解決爭議"])
    if disputed_findings:
        lines.extend(_finding_lines(disputed_findings))
    elif verification_summary and verification_summary.unavailable_required:
        lines.extend(_bullet_lines(verification_summary.unavailable_required))
    else:
        lines.append("- 目前沒有額外待人工裁決的項目。")

    return "\n".join(lines).strip() + "\n"


def build_next_steps_markdown(
    *,
    review_profile: ReviewProfile,
    session_reports: list[SessionReport],
    verdict: GateVerdict | None = None,
    verification_summary: VerificationSummary | None = None,
    consensus_findings: list[Finding] | None = None,
    disputed_findings: list[Finding] | None = None,
) -> str:
    consensus_findings = consensus_findings or []
    disputed_findings = disputed_findings or []
    immediate = []
    confirm = []
    backlog = []

    if review_profile == ReviewProfile.LLM_REPO:
        for finding in consensus_findings:
            target = (
                immediate
                if finding.severity in {FindingSeverity.BLOCKING, FindingSeverity.MAJOR}
                else backlog
            )
            target.append(_finding_action_line(finding))
        for finding in disputed_findings:
            confirm.append(_finding_action_line(finding))
        if verification_summary:
            for check in verification_summary.checks:
                if check.status == "failed":
                    label = verification_check_title(check)
                    if (
                        check.role == VerificationRole.CANONICAL
                        and check.applicability == VerificationApplicability.REQUIRED
                    ):
                        immediate.append(f"修正並重新執行 `{label}`：{check.summary}")
                    elif check.kind_hint == FindingKind.LABEL_MISMATCH:
                        confirm.append(f"重新界定 `{label}` 的測試層級與命名：{check.summary}")
                    else:
                        confirm.append(f"確認 `{label}` 是否應視為正式 gate：{check.summary}")
                elif check.status == "unavailable":
                    confirm.append(
                        "確認 "
                        f"`{verification_check_title(check)}` "
                        f"是否應配置於此 repo：{check.summary}"
                    )
                elif check.status == "skipped":
                    target = confirm if check.kind_hint == FindingKind.COVERAGE_GAP else backlog
                    target.append(
                        "確認 "
                        f"`{verification_check_title(check)}` "
                        f"是否需要納入後續驗證：{check.summary}"
                    )
    else:
        for report in session_reports:
            problems, _, suggestions = _general_session_signals(report.report_markdown or "")
            immediate.extend(f"{report.display_name}：{item}" for item in problems[:2])
            backlog.extend(f"{report.display_name}：{item}" for item in suggestions[:2])
        if not immediate:
            confirm.append(
                "本次一般模式未整理出阻擋項目，建議先人工確認高風險模組是否需要進一步審查。"
            )

    if verdict == GateVerdict.FAIL:
        immediate.insert(0, "先處理所有阻擋與重大問題，再考慮合併或發布。")
    elif verdict == GateVerdict.NEEDS_HUMAN_REVIEW:
        confirm.insert(0, "本次結果仍需人工判讀，請先確認需求、規格邊界與爭議項目。")

    rerun = []
    if verification_summary:
        rerun.extend(
            f"`{verification_check_title(check)}` 修正或配置完成後，請重新執行驗證。"
            for check in verification_summary.checks
            if check.status in {"failed", "unavailable"}
        )
    if not rerun:
        rerun.append("修正完成後，重新執行 Reviewer，確認問題是否已消失且沒有引入新的分歧。")

    lines = [
        "# 建議下一步操作",
        "",
        "## 立即處理",
        *(_bullet_lines(_dedupe(immediate)) or ["- 目前沒有需要立即處理的阻擋項目。"]),
        "",
        "## 先確認再處理",
        *(_bullet_lines(_dedupe(confirm)) or ["- 目前沒有額外需要人工確認的項目。"]),
        "",
        "## 可排入後續優化",
        *(_bullet_lines(_dedupe(backlog)) or ["- 目前沒有額外的後續優化建議。"]),
        "",
        "## 修正後建議重新驗證的項目",
        *(_bullet_lines(_dedupe(rerun)) or ["- 目前沒有指定的重新驗證項目。"]),
    ]
    return "\n".join(lines).strip() + "\n"


def _session_header_lines(
    *,
    title: str,
    agent_id: str,
    display_name: str,
    model: str | None,
    status: str,
    duration_ms: int | None,
    metrics: SessionMetrics,
    tool_call_count: int,
) -> list[str]:
    duration_label = f"{round(duration_ms / 1000, 2)}s" if duration_ms is not None else "未提供"
    return [
        f"# {title}",
        "",
        f"- 角色 ID：`{agent_id}`",
        f"- 顯示名稱：{display_name}",
        f"- 模型：`{model or '未提供'}`",
        f"- 狀態：{status}",
        f"- 執行時間：{duration_label}",
        f"- 工具呼叫數：{tool_call_count}",
        "- Tokens："
        f"{metrics.total_tokens}（輸入 {metrics.input_tokens} / 輸出 {metrics.output_tokens}）",
    ]


def _profile_label(profile: ReviewProfile) -> str:
    return "LLM Repo 嚴格模式" if profile == ReviewProfile.LLM_REPO else "一般模式"


def _finding_lines(findings: Iterable[Finding]) -> list[str]:
    lines: list[str] = []
    for item in findings:
        severity = _SEVERITY_LABELS.get(item.severity, item.severity.value)
        evidence = (
            ", ".join(ref.label or ref.path or ref.kind for ref in item.evidence_refs[:3])
            or "未提供證據"
        )
        lines.append(f"- [{severity}] {item.summary}（證據：{evidence}）")
    return lines


def verification_status_label(check: VerificationCheckResult) -> str:
    if check.kind_hint == FindingKind.COVERAGE_GAP:
        return "部分覆蓋"
    if check.kind_hint == FindingKind.LABEL_MISMATCH and check.status == "failed":
        return "標籤失真"
    if check.kind_hint == FindingKind.ENV_GAP and check.status == "skipped":
        return "未偵測"
    return _VERIFICATION_STATUS_LABELS.get(check.status, check.status)


def verification_check_title(check: VerificationCheckResult) -> str:
    title = check.display_name or check.name
    if check.scope != "repo-wide":
        return f"{title} [{check.scope}]"
    return title


def verification_context_label(check: VerificationCheckResult) -> str:
    parts: list[str] = [check.role.value, check.applicability.value]
    if check.working_dir not in {"", "."}:
        parts.append(check.working_dir)
    return " / ".join(part for part in parts if part)


def _verification_lines(checks: Iterable[VerificationCheckResult]) -> list[str]:
    lines: list[str] = []
    for check in checks:
        label = verification_status_label(check)
        context = verification_context_label(check)
        detail = f"（{context}）" if context else ""
        lines.append(f"- `{verification_check_title(check)}`：{label}，{check.summary}{detail}")
    return lines


def _bullet_lines(items: Iterable[str]) -> list[str]:
    return [f"- {item}" for item in items if item and item.strip()]


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _session_summary(
    report: SessionReport, review_profile: ReviewProfile
) -> tuple[list[str], list[str], list[str]]:
    if review_profile == ReviewProfile.LLM_REPO:
        problems = [
            f"[{_SEVERITY_LABELS.get(item.severity, item.severity.value)}] {item.summary}"
            for item in report.findings
        ]
        strengths = report.no_issue_sections
        evidence = _verification_lines(report.verification_checks)
        if report.audited_dimensions:
            evidence.insert(0, "審查維度：" + ", ".join(report.audited_dimensions))
        if report.rationale_markdown.strip():
            evidence.append("補充判斷：" + _first_meaningful_paragraph(report.rationale_markdown))
        return problems, strengths, evidence

    problems, strengths, suggestions = _general_session_signals(report.report_markdown or "")
    evidence = [f"工具呼叫 {report.tool_call_count} 次", f"總 token {report.metrics.total_tokens}"]
    if suggestions:
        evidence.append("其他建議：" + "；".join(suggestions[:2]))
    return problems, strengths, evidence


def _general_session_signals(markdown: str) -> tuple[list[str], list[str], list[str]]:
    sections = _extract_markdown_sections(markdown)
    problems = [
        *sections.get("重大問題", []),
        *sections.get("重要問題", []),
    ]
    strengths = sections.get("優點", [])
    suggestions = sections.get("建議", [])
    return problems, strengths, suggestions


def _extract_markdown_sections(markdown: str) -> dict[str, list[str]]:
    results: dict[str, list[str]] = {}
    current: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer, current
        if current is None:
            buffer = []
            return
        content = "\n".join(buffer).strip()
        results[current] = _split_markdown_items(content)
        buffer = []

    for raw_line in markdown.splitlines():
        heading = _match_general_heading(raw_line)
        if heading:
            flush()
            current = heading
            continue
        if current is not None:
            buffer.append(raw_line)
    flush()
    return results


def _match_general_heading(line: str) -> str | None:
    normalized = re.sub(r"^#+\s*", "", line).strip().lower()
    for title, aliases in _GENERAL_SECTION_ALIASES.items():
        if normalized in aliases:
            return title
    return None


def _split_markdown_items(content: str) -> list[str]:
    if not content:
        return []
    items = []
    current: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                items.append(" ".join(current).strip())
                current = []
            continue
        if re.match(r"^[-*]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
            if current:
                items.append(" ".join(current).strip())
            current = [re.sub(r"^([-*]|\d+\.)\s+", "", stripped)]
            continue
        current.append(stripped)
    if current:
        items.append(" ".join(current).strip())
    return items


def _first_meaningful_paragraph(text: str) -> str:
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        cleaned = paragraph.strip()
        if cleaned and not cleaned.startswith("#"):
            return cleaned
    return text.strip() or "未提供。"


def _finding_action_line(finding: Finding) -> str:
    severity = _SEVERITY_LABELS.get(finding.severity, finding.severity.value)
    return f"[{severity}] {finding.summary}；建議：{finding.suggested_fix}"
