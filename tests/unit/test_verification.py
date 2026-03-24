import asyncio
import subprocess

from backend.orchestration.strict_types import (
    FindingKind,
    VerificationApplicability,
    VerificationRole,
)
from backend.orchestration.verification import run_verification


def _which(command: str) -> str | None:
    mapping = {
        "uv": "uv",
        "npm": "npm",
        "python": "python",
        "pytest": "pytest",
        "ruff": "ruff",
        "mypy": "mypy",
    }
    return mapping.get(command)


def test_run_verification_keeps_active_frontend_checks_out_of_stale_bucket(
    tmp_codebase, monkeypatch
):
    (tmp_codebase / "pyproject.toml").write_text(
        "[project]\nname='demo'\n[tool.pytest.ini_options]\ntestpaths=['tests']\n",
        encoding="utf-8",
    )
    (tmp_codebase / "tests").mkdir()
    (tmp_codebase / "tests" / "test_main.py").write_text("def test_ok():\n    assert True\n")
    (tmp_codebase / "src" / "frontend").mkdir(parents=True)
    (tmp_codebase / "src" / "frontend" / "package.json").write_text(
        '{"name":"frontend","scripts":{"test":"vitest","build":"vite build"}}',
        encoding="utf-8",
    )
    (tmp_codebase / "src" / "frontend" / "src").mkdir()
    (tmp_codebase / "src" / "frontend" / "src" / "App.jsx").write_text(
        "export default function App() {}", encoding="utf-8"
    )

    monkeypatch.setattr("backend.orchestration.verification.shutil.which", _which)
    monkeypatch.setattr(
        "backend.orchestration.verification.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "ok", ""),
    )

    summary = asyncio.run(
        run_verification(
            str(tmp_codebase),
            "static_runtime",
            selected_paths=["src/frontend/src/App.jsx"],
        )
    )

    frontend_test = next(
        check for check in summary.checks if check.scope == "frontend" and check.name == "tests"
    )
    root_test = next(
        check for check in summary.checks if check.scope == "repo-wide" and check.name == "tests"
    )

    assert frontend_test.role == VerificationRole.SUPPLEMENTAL
    assert frontend_test.applicability == VerificationApplicability.OPTIONAL
    assert root_test.role == VerificationRole.CANONICAL
    assert root_test.applicability == VerificationApplicability.REQUIRED


def test_run_verification_marks_unrelated_frontend_scripts_as_stale_suspect(
    tmp_codebase, monkeypatch
):
    (tmp_codebase / "pyproject.toml").write_text(
        "[project]\nname='demo'\n[tool.pytest.ini_options]\ntestpaths=['tests']\n",
        encoding="utf-8",
    )
    (tmp_codebase / "tests").mkdir()
    (tmp_codebase / "tests" / "test_main.py").write_text("def test_ok():\n    assert True\n")
    (tmp_codebase / "src" / "frontend").mkdir(parents=True)
    (tmp_codebase / "src" / "frontend" / "package.json").write_text(
        '{"name":"frontend","scripts":{"test":"vitest"}}',
        encoding="utf-8",
    )

    monkeypatch.setattr("backend.orchestration.verification.shutil.which", _which)
    monkeypatch.setattr(
        "backend.orchestration.verification.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "ok", ""),
    )

    summary = asyncio.run(
        run_verification(
            str(tmp_codebase),
            "static_runtime",
            selected_paths=["src/backend/main.py"],
        )
    )

    frontend_test = next(
        check for check in summary.checks if check.scope == "frontend" and check.name == "tests"
    )

    assert frontend_test.role == VerificationRole.STALE_SUSPECT
    assert frontend_test.applicability == VerificationApplicability.OPTIONAL


def test_run_verification_surfaces_env_gated_coverage(tmp_codebase, monkeypatch):
    (tmp_codebase / "pyproject.toml").write_text(
        """
[project]
name = "demo"
[tool.pytest.ini_options]
markers = [
    "integration: marks tests that require a running service (deselect with -m 'not integration')",
]
""".strip(),
        encoding="utf-8",
    )
    (tmp_codebase / "tests" / "integration").mkdir(parents=True)
    (tmp_codebase / "tests" / "integration" / "test_api.py").write_text(
        '''
"""
Integration tests are skipped by default.
"""

import pytest

@pytest.mark.integration
def test_live_client():
    pytest.skip("Requires live service")
'''.strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr("backend.orchestration.verification.shutil.which", lambda command: None)

    summary = asyncio.run(run_verification(str(tmp_codebase), "static_runtime"))

    coverage = next(check for check in summary.checks if check.name == "e2e_coverage")

    assert coverage.kind_hint == FindingKind.COVERAGE_GAP
    assert coverage.status == "skipped"
    assert coverage.applicability == VerificationApplicability.ENV_GATED
    assert (
        summary.verdict_predicate
        == "no canonical blocking failures, but significant coverage gaps remain"
    )


def test_run_verification_flags_mock_heavy_integration_labels(tmp_codebase, monkeypatch):
    (tmp_codebase / "tests" / "integration").mkdir(parents=True)
    (tmp_codebase / "tests" / "integration" / "test_api.py").write_text(
        """
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

@pytest.mark.integration
def test_endpoint():
    session = MagicMock()
    event = MagicMock()
    other = AsyncMock()
    with patch("app.service.handler") as handler:
        handler.return_value = session
        session.fetch.return_value = event
        assert other is not None
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr("backend.orchestration.verification.shutil.which", lambda command: None)

    summary = asyncio.run(run_verification(str(tmp_codebase), "static_runtime"))

    label_check = next(
        check for check in summary.checks if check.name == "integration_label_fidelity"
    )

    assert label_check.kind_hint == FindingKind.LABEL_MISMATCH
    assert label_check.status == "failed"
    assert label_check.role == VerificationRole.SUPPLEMENTAL
