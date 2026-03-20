from pathlib import Path

from backend.orchestration.model_router import ModelPreset, ModelRouter
from backend.orchestration.review_estimator import estimate_review_cost
from backend.orchestration.strict_types import ConvergenceMode, ReviewProfile
from backend.review_inputs import normalize_local_review_input


class TestReviewEstimator:
    def test_general_estimate_returns_five_fixed_roles(self, tmp_codebase: Path):
        normalized = normalize_local_review_input(
            source_mode="folder",
            folder_path=str(tmp_codebase),
            file_paths=None,
            uploaded_files=None,
            focus_prompt="Review this repo.",
        )

        estimate = estimate_review_cost(
            normalized_input=normalized,
            review_profile=ReviewProfile.GENERAL,
            convergence_mode=ConvergenceMode.SINGLE_PASS,
            model_router=ModelRouter(preset=ModelPreset.BALANCED),
            model_billing_multipliers={"claude-sonnet-4.6": 1.0},
        )

        assert estimate.estimated_sessions_min == 5
        assert estimate.estimated_sessions_max == 5
        assert [item.role for item in estimate.role_estimates] == [
            "orchestrator",
            "reviewer_1",
            "reviewer_2",
            "reviewer_3",
            "synthesizer",
        ]
        assert estimate.estimated_pru_min == 5.0
        assert estimate.estimated_pru_max == 5.0

    def test_strict_estimate_includes_optional_challenger(self, tmp_codebase: Path):
        (tmp_codebase / "SPEC.md").write_text("# Spec\n")
        (tmp_codebase / "docs").mkdir()
        (tmp_codebase / "docs" / "adr-001.md").write_text("# ADR\n")
        (tmp_codebase / "tests").mkdir()
        (tmp_codebase / "tests" / "test_main.py").write_text("def test_ok():\n    assert True\n")
        (tmp_codebase / ".github").mkdir()
        (tmp_codebase / ".github" / "workflows").mkdir()
        (tmp_codebase / ".github" / "workflows" / "ci.yml").write_text("name: ci\n")

        normalized = normalize_local_review_input(
            source_mode="folder",
            folder_path=str(tmp_codebase),
            file_paths=None,
            uploaded_files=None,
            focus_prompt="Find drift and artifacts.",
        )

        estimate = estimate_review_cost(
            normalized_input=normalized,
            review_profile=ReviewProfile.LLM_REPO,
            convergence_mode=ConvergenceMode.ADAPTIVE_RERUN,
            model_router=ModelRouter(preset=ModelPreset.BALANCED),
            model_billing_multipliers={
                "claude-sonnet-4.6": 1.0,
                "claude-opus-4.6": 2.0,
            },
        )

        challenger = next(item for item in estimate.role_estimates if item.role == "challenger")
        assert estimate.estimated_sessions_min >= 7
        assert estimate.estimated_sessions_max >= estimate.estimated_sessions_min
        assert challenger.optional is True
        assert challenger.estimated_sessions_min == 0
        assert challenger.estimated_sessions_max == 1
        assert challenger.estimated_pru_min == 0.0
        assert challenger.estimated_pru_max == challenger.billing_multiplier
        assert estimate.estimated_pru_max >= estimate.estimated_pru_min
