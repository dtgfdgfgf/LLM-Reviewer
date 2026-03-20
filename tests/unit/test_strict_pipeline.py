from pathlib import Path

from backend.orchestration.strict_pipeline import (
    BudgetManager,
    _deterministic_verdict,
    _verification_findings,
    build_llm_review_plan,
)
from backend.orchestration.strict_types import (
    DriftType,
    Finding,
    FindingKind,
    FindingSeverity,
    GateVerdict,
    SpecialistAssignment,
    VerificationApplicability,
    VerificationCheckResult,
    VerificationRole,
    VerificationSummary,
)


class TestStrictPlanner:
    def test_build_plan_creates_assignments_for_all_specialists(self, tmp_codebase: Path):
        (tmp_codebase / "SPEC.md").write_text("# Spec\n")
        (tmp_codebase / "docs").mkdir()
        (tmp_codebase / "docs" / "adr-001.md").write_text("# ADR\n")
        (tmp_codebase / ".github").mkdir()
        (tmp_codebase / ".github" / "workflows").mkdir()
        (tmp_codebase / ".github" / "workflows" / "ci.yml").write_text("name: ci\n")

        plan = build_llm_review_plan(str(tmp_codebase), "Focus on drift.")

        assert plan.shared_core_files
        assert plan.artifact_files
        roles = {assignment.role for assignment in plan.assignments}
        assert roles == {
            "spec_drift",
            "architecture_integrity",
            "security_boundary",
            "runtime_operational",
            "test_integrity",
            "llm_artifact_simplification",
        }

    def test_budget_manager_shards_large_assignment(self, tmp_codebase: Path):
        large_files = []
        for index in range(5):
            path = tmp_codebase / "src" / f"module_{index}.py"
            path.write_text("x" * 50_000)
            large_files.append(f"src/module_{index}.py")

        assignment = SpecialistAssignment(
            agent_id="runtime_operational",
            role="runtime_operational",
            display_name="Runtime",
            shared_core_files=[],
            artifact_files=[],
            role_extra_files=large_files,
            focus="Runtime",
            risk_hypotheses=[],
        )
        manager = BudgetManager(context_window=10_000, primary_ratio=0.2, judge_ratio=0.3)

        shards = manager.shard_assignment(tmp_codebase, assignment, [])

        assert len(shards) > 1
        assert all(shard.agent_id.startswith("runtime_operational__") for shard in shards)


class TestStrictVerdicts:
    def test_verification_failures_become_blocking_findings(self):
        summary = VerificationSummary(
            status="complete",
            checks=[
                VerificationCheckResult(
                    name="tests",
                    status="failed",
                    summary="Tests failed",
                    blocking=True,
                )
            ],
            blocking_failures=["tests"],
        )

        findings = _verification_findings(summary)

        assert len(findings) == 1
        assert findings[0].severity == FindingSeverity.BLOCKING
        assert findings[0].drift_type == DriftType.RUNTIME
        assert findings[0].kind == FindingKind.RUNTIME_FAILURE

    def test_missing_required_evidence_requires_human_review(self):
        findings = [
            Finding(
                id="f1",
                category="spec",
                severity=FindingSeverity.MAJOR,
                drift_type=DriftType.SPEC,
                summary="Drift",
                evidence_refs=[],
                confidence=0.7,
                why_it_matters="matters",
                suggested_fix="fix",
                gate_impact="impact",
                cluster_key="spec::1",
            )
        ]
        verification = VerificationSummary(
            status="complete",
            checks=[],
            unavailable_required=["runtime_detection"],
        )

        verdict = _deterministic_verdict(findings, [], verification)

        assert verdict == GateVerdict.NEEDS_HUMAN_REVIEW

    def test_coverage_gap_verification_requires_human_review(self):
        findings = _verification_findings(
            VerificationSummary(
                status="complete",
                checks=[
                    VerificationCheckResult(
                        name="db_coverage",
                        display_name="DB-backed coverage",
                        status="skipped",
                        summary="預設測試 gate 未涵蓋 DB / persistence 路徑。",
                        role=VerificationRole.CANONICAL,
                        applicability=VerificationApplicability.ENV_GATED,
                        scope="db",
                        kind_hint=FindingKind.COVERAGE_GAP,
                        blocking=False,
                    )
                ],
            )
        )

        verdict = _deterministic_verdict(findings, [], VerificationSummary(status="complete", checks=[]))

        assert findings[0].kind == FindingKind.COVERAGE_GAP
        assert verdict == GateVerdict.NEEDS_HUMAN_REVIEW
