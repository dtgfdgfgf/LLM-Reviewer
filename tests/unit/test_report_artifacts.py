import asyncio
from pathlib import Path

from backend.orchestration.report_artifacts import (
    SessionMetrics,
    build_final_summary_markdown,
    build_next_steps_markdown,
    build_strict_session_report,
)
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
from backend.orchestration.verification import run_verification


def test_final_summary_includes_no_issue_sections_and_evidence_density():
    finding = Finding(
        id="f1",
        category="spec",
        severity=FindingSeverity.MAJOR,
        drift_type="spec",
        summary="登入流程與規格文件不一致。",
        evidence_refs=[],
        confidence=0.8,
        why_it_matters="會讓使用者看到未預期的驗證行為。",
        suggested_fix="同步更新實作與規格文件。",
        gate_impact="需要先修正再發布。",
        cluster_key="spec::login",
    )
    session_report = build_strict_session_report(
        agent_id="spec_drift",
        display_name="規格漂移",
        model="claude-sonnet-4-6",
        status="complete",
        started_at=1,
        completed_at=2,
        duration_ms=1,
        metrics=SessionMetrics(input_tokens=10, output_tokens=5),
        tool_call_count=2,
        raw_output="已提交結構化 findings。",
        findings=[finding],
        no_issue_sections=["測試案例與需求敘述保持一致。"],
        audited_dimensions=["spec_drift"],
        rationale_markdown="這是本次最明顯的規格漂移。",
        verification_checks=[],
    )

    summary = build_final_summary_markdown(
        review_profile=ReviewProfile.LLM_REPO,
        final_report="## 結論\n需要先修正登入規格漂移。",
        session_reports=[session_report],
        verdict=GateVerdict.FAIL,
        verification_summary=VerificationSummary(status="complete", checks=[]),
        convergence_metrics=ConvergenceMetrics(evidence_density=2.0),
        drift_summary=DriftSummary(summary="主要漂移型態：spec"),
        consensus_findings=[finding],
        disputed_findings=[],
    )

    assert "表現良好" in summary
    assert "測試案例與需求敘述保持一致。" in summary
    assert "證據密度：2.0（每個 finding 平均引用的證據數）" in summary


def test_final_summary_uses_verification_predicate_and_context():
    summary = build_final_summary_markdown(
        review_profile=ReviewProfile.LLM_REPO,
        final_report="需要人工確認 coverage gap。",
        session_reports=[],
        verdict=GateVerdict.NEEDS_HUMAN_REVIEW,
        verification_summary=VerificationSummary(
            status="complete",
            verdict_predicate=(
                "no canonical blocking failures, but significant coverage gaps remain"
            ),
            checks=[
                VerificationCheckResult(
                    name="e2e_coverage",
                    display_name="Integration coverage",
                    status="skipped",
                    summary="預設測試 gate 未涵蓋 live integration / e2e 路徑。",
                    role=VerificationRole.CANONICAL,
                    applicability=VerificationApplicability.ENV_GATED,
                    scope="e2e",
                    kind_hint=FindingKind.COVERAGE_GAP,
                    blocking=False,
                )
            ],
        ),
        convergence_metrics=ConvergenceMetrics(),
        drift_summary=DriftSummary(summary="主要漂移型態：test"),
    )

    assert (
        "裁決依據：no canonical blocking failures, but significant coverage gaps remain" in summary
    )
    assert "Integration coverage [e2e]" in summary
    assert "部分覆蓋" in summary


def test_next_steps_groups_actions_for_strict_mode():
    finding = Finding(
        id="f2",
        category="runtime",
        severity=FindingSeverity.BLOCKING,
        drift_type="runtime",
        summary="必要的建置檢查失敗。",
        evidence_refs=[],
        confidence=1.0,
        why_it_matters="目前產物不可安全發布。",
        suggested_fix="修正建置設定後重新驗證。",
        gate_impact="阻擋發布。",
        cluster_key="runtime::build",
    )
    next_steps = build_next_steps_markdown(
        review_profile=ReviewProfile.LLM_REPO,
        session_reports=[],
        verdict=GateVerdict.FAIL,
        verification_summary=VerificationSummary(status="complete", checks=[]),
        consensus_findings=[finding],
        disputed_findings=[],
    )

    assert "## 立即處理" in next_steps
    assert "先處理所有阻擋與重大問題" in next_steps
    assert "必要的建置檢查失敗" in next_steps


def test_run_verification_without_safe_commands_marks_runtime_detection_as_skipped(tmp_path: Path):
    root = tmp_path / "empty-repo"
    root.mkdir()

    summary = asyncio.run(run_verification(str(root), "static_runtime"))

    assert summary.status == "complete"
    assert summary.unavailable_required == []
    assert summary.checks[0].name == "runtime_detection"
    assert summary.checks[0].status == "skipped"
    assert summary.checks[0].blocking is False
