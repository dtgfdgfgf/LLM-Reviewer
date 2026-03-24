"""
Pre-flight cost estimation for review runs.

The estimator provides a bounded, UI-friendly approximation of session count
and premium-request usage before a review starts. Estimates are intentionally
expressed as ranges because challenger execution, strict-mode sharding, and
agent turn counts vary with the repo and findings discovered at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from backend.orchestration.model_router import AgentRole, ModelRouter
from backend.orchestration.strict_pipeline import BudgetManager, build_llm_review_plan
from backend.orchestration.strict_types import ConvergenceMode, ReviewProfile
from backend.review_inputs import NormalizedReviewInput
from backend.tools.codebase import is_supported_text_file

_GENERAL_ROLE_ORDER = [
    AgentRole.ORCHESTRATOR,
    AgentRole.REVIEWER_1,
    AgentRole.REVIEWER_2,
    AgentRole.REVIEWER_3,
    AgentRole.SYNTHESIZER,
]

_STRICT_PRIMARY_ROLE_ORDER = [
    ("spec_drift", AgentRole.SPEC_DRIFT, "Spec Drift"),
    ("architecture_integrity", AgentRole.ARCHITECTURE_INTEGRITY, "Architecture"),
    ("security_boundary", AgentRole.SECURITY_BOUNDARY, "Security"),
    ("runtime_operational", AgentRole.RUNTIME_OPERATIONAL, "Runtime"),
    ("test_integrity", AgentRole.TEST_INTEGRITY, "Test Integrity"),
    ("llm_artifact_simplification", AgentRole.LLM_ARTIFACT_SIMPLIFICATION, "LLM Artifact"),
]

_GENERAL_DISPLAY_NAMES = {
    AgentRole.ORCHESTRATOR: "Orchestrator",
    AgentRole.REVIEWER_1: "Architecture",
    AgentRole.REVIEWER_2: "Backend",
    AgentRole.REVIEWER_3: "Frontend",
    AgentRole.SYNTHESIZER: "Synthesizer",
}


@dataclass
class RoleEstimate:
    role: str
    display_name: str
    model: str
    billing_multiplier: float
    estimated_sessions_min: int
    estimated_sessions_max: int
    estimated_turns_min: int
    estimated_turns_max: int
    estimated_pru_min: float
    estimated_pru_max: float
    optional: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class ReviewEstimate:
    review_profile: ReviewProfile
    source_mode: str
    estimated_sessions_min: int
    estimated_sessions_max: int
    estimated_turns_min: int
    estimated_turns_max: int
    estimated_pru_min: float
    estimated_pru_max: float
    role_estimates: list[RoleEstimate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def estimate_review_cost(
    *,
    normalized_input: NormalizedReviewInput,
    review_profile: ReviewProfile,
    convergence_mode: ConvergenceMode,
    model_router: ModelRouter,
    model_billing_multipliers: dict[str, float] | None = None,
) -> ReviewEstimate:
    billing = model_billing_multipliers or {}
    if review_profile == ReviewProfile.LLM_REPO:
        return _estimate_strict(
            normalized_input=normalized_input,
            convergence_mode=convergence_mode,
            model_router=model_router,
            model_billing_multipliers=billing,
        )
    return _estimate_general(
        normalized_input=normalized_input,
        model_router=model_router,
        model_billing_multipliers=billing,
    )


def _estimate_general(
    *,
    normalized_input: NormalizedReviewInput,
    model_router: ModelRouter,
    model_billing_multipliers: dict[str, float],
) -> ReviewEstimate:
    file_count, total_bytes = _scope_stats(normalized_input)
    complexity = _scope_complexity(file_count, total_bytes)
    role_estimates: list[RoleEstimate] = []
    notes = [
        "這是送出前的範圍估算；Premium Requests 依 GitHub 官方規則按 "
        "billable session × model multiplier 計算。",
    ]

    for role in _GENERAL_ROLE_ORDER:
        turns_min, turns_max = _general_turn_band(role, complexity)
        model = model_router.get_model(role)
        multiplier = model_billing_multipliers.get(model, 1.0)
        role_estimates.append(
            RoleEstimate(
                role=role.value,
                display_name=_GENERAL_DISPLAY_NAMES[role],
                model=model,
                billing_multiplier=multiplier,
                estimated_sessions_min=1,
                estimated_sessions_max=1,
                estimated_turns_min=turns_min,
                estimated_turns_max=turns_max,
                estimated_pru_min=_round_cost(1 * multiplier),
                estimated_pru_max=_round_cost(1 * multiplier),
            )
        )

    if file_count > 40:
        notes.append("檔案數偏多，orchestrator 與 reviewers 可能會多做幾輪探索。")

    return _aggregate_estimate(
        review_profile=ReviewProfile.GENERAL,
        source_mode=normalized_input.source_mode,
        role_estimates=role_estimates,
        notes=notes,
    )


def _estimate_strict(
    *,
    normalized_input: NormalizedReviewInput,
    convergence_mode: ConvergenceMode,
    model_router: ModelRouter,
    model_billing_multipliers: dict[str, float],
) -> ReviewEstimate:
    root = Path(normalized_input.review_root)
    budget_manager = BudgetManager()
    plan = build_llm_review_plan(normalized_input.review_root, normalized_input.focus_prompt)
    role_estimates: list[RoleEstimate] = []
    notes = [
        "這是送出前的範圍估算；Premium Requests 依 GitHub 官方規則按 "
        "billable session × model multiplier 計算。",
        "challenger 是否啟用取決於 findings 與衝突程度。",
    ]
    sharded_roles = 0

    for role_name, agent_role, display_name in _STRICT_PRIMARY_ROLE_ORDER:
        assignment = next(item for item in plan.assignments if item.role == role_name)
        shards = budget_manager.shard_assignment(root, assignment, [])
        turns_min = 0
        turns_max = 0
        for shard in shards:
            shard_min, shard_max = _strict_turn_band(
                shard.estimated_tokens,
                budget_manager.primary_limit,
            )
            turns_min += shard_min
            turns_max += shard_max
        model = model_router.get_model(agent_role)
        multiplier = model_billing_multipliers.get(model, 1.0)
        shard_notes: list[str] = []
        if len(shards) > 1:
            sharded_roles += 1
            shard_notes.append(
                "內容量可能超過單一 context 預算，這個角色會被拆成多個 reviewer session。"
            )

        role_estimates.append(
            RoleEstimate(
                role=role_name,
                display_name=display_name,
                model=model,
                billing_multiplier=multiplier,
                estimated_sessions_min=len(shards),
                estimated_sessions_max=len(shards),
                estimated_turns_min=turns_min,
                estimated_turns_max=turns_max,
                estimated_pru_min=_round_cost(len(shards) * multiplier),
                estimated_pru_max=_round_cost(len(shards) * multiplier),
                notes=shard_notes,
            )
        )

    judge_turns_min, judge_turns_max = _judge_turn_band(role_estimates)
    judge_model = model_router.get_model(AgentRole.JUDGE)
    judge_multiplier = model_billing_multipliers.get(judge_model, 1.0)
    role_estimates.append(
        RoleEstimate(
            role=AgentRole.JUDGE.value,
            display_name="Judge",
            model=judge_model,
            billing_multiplier=judge_multiplier,
            estimated_sessions_min=1,
            estimated_sessions_max=1,
            estimated_turns_min=judge_turns_min,
            estimated_turns_max=judge_turns_max,
            estimated_pru_min=_round_cost(1 * judge_multiplier),
            estimated_pru_max=_round_cost(1 * judge_multiplier),
        )
    )

    if convergence_mode in {ConvergenceMode.ADAPTIVE_RERUN, ConvergenceMode.FIXED_DOUBLE_PASS}:
        challenger_turns_min = 0
        challenger_turns_max = 2 + (1 if sharded_roles >= 2 else 0)
        challenger_model = model_router.get_model(AgentRole.CHALLENGER)
        challenger_multiplier = model_billing_multipliers.get(challenger_model, 1.0)
        role_estimates.append(
            RoleEstimate(
                role=AgentRole.CHALLENGER.value,
                display_name="Challenger",
                model=challenger_model,
                billing_multiplier=challenger_multiplier,
                estimated_sessions_min=0,
                estimated_sessions_max=1,
                estimated_turns_min=challenger_turns_min,
                estimated_turns_max=challenger_turns_max,
                estimated_pru_min=0.0,
                estimated_pru_max=_round_cost(1 * challenger_multiplier),
                optional=True,
                notes=["只有當 blocking / disputed findings 需要 challenge 時才會啟動。"],
            )
        )

    if sharded_roles:
        notes.append(
            f"預估有 {sharded_roles} 個 strict reviewer 角色可能因 context 預算而拆 shard。"
        )

    return _aggregate_estimate(
        review_profile=ReviewProfile.LLM_REPO,
        source_mode=normalized_input.source_mode,
        role_estimates=role_estimates,
        notes=notes,
    )


def _aggregate_estimate(
    *,
    review_profile: ReviewProfile,
    source_mode: str,
    role_estimates: list[RoleEstimate],
    notes: list[str],
) -> ReviewEstimate:
    return ReviewEstimate(
        review_profile=review_profile,
        source_mode=source_mode,
        estimated_sessions_min=sum(item.estimated_sessions_min for item in role_estimates),
        estimated_sessions_max=sum(item.estimated_sessions_max for item in role_estimates),
        estimated_turns_min=sum(item.estimated_turns_min for item in role_estimates),
        estimated_turns_max=sum(item.estimated_turns_max for item in role_estimates),
        estimated_pru_min=_round_cost(sum(item.estimated_pru_min for item in role_estimates)),
        estimated_pru_max=_round_cost(sum(item.estimated_pru_max for item in role_estimates)),
        role_estimates=role_estimates,
        notes=notes,
    )


def _scope_stats(normalized_input: NormalizedReviewInput) -> tuple[int, int]:
    root = Path(normalized_input.review_root)
    rel_paths = (
        normalized_input.selected_paths
        if normalized_input.selected_paths
        else _iter_reviewable_files(root)
    )
    total_bytes = 0
    for rel_path in rel_paths:
        try:
            total_bytes += (root / rel_path).stat().st_size
        except OSError:
            continue
    return len(rel_paths), total_bytes


def _iter_reviewable_files(root: Path) -> list[str]:
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
    files: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        supported, _ = is_supported_text_file(path)
        if not supported:
            continue
        try:
            files.append(path.relative_to(root).as_posix())
        except ValueError:
            continue
    return sorted(files)


def _scope_complexity(file_count: int, total_bytes: int) -> int:
    score = 0
    if file_count > 5:
        score += 1
    if file_count > 20:
        score += 1
    if file_count > 60:
        score += 1
    if total_bytes > 120_000:
        score += 1
    if total_bytes > 500_000:
        score += 1
    return score


def _general_turn_band(role: AgentRole, complexity: int) -> tuple[int, int]:
    if role == AgentRole.ORCHESTRATOR:
        return 2 + min(complexity, 1), 3 + min(complexity, 2)
    if role == AgentRole.SYNTHESIZER:
        return 1 + (1 if complexity >= 2 else 0), 2 + (1 if complexity >= 4 else 0)
    return 2 + min(complexity // 2, 1), 3 + min(complexity, 2)


def _strict_turn_band(estimated_tokens: int, budget_limit: int) -> tuple[int, int]:
    ratio = estimated_tokens / max(budget_limit, 1)
    if ratio < 0.20:
        return 2, 2
    if ratio < 0.45:
        return 2, 3
    if ratio < 0.70:
        return 3, 4
    return 4, 5


def _judge_turn_band(primary_role_estimates: list[RoleEstimate]) -> tuple[int, int]:
    session_count = sum(item.estimated_sessions_max for item in primary_role_estimates)
    if session_count <= 6:
        return 2, 3
    if session_count <= 8:
        return 3, 4
    return 4, 5


def _round_cost(value: float) -> float:
    return round(value, 2)
