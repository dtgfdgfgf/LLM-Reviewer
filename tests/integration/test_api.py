"""
FastAPI endpoint contract tests.

These tests use mocked sessions and run as part of the default pytest suite.
Live Copilot CLI coverage lives in a separate module marked with
``pytest.mark.integration``.

Run this file: uv run pytest tests/integration/test_api.py -v
Skip live CLI tests elsewhere: uv run pytest -m "not integration" -v
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.api.routes import app_control as app_control_module
from backend.app_runtime import AppRuntime
from backend.main import create_app
from backend.orchestration.event_bus import EventBus
from backend.orchestration.review_store import ReviewStore


@pytest.fixture
def mock_session_manager():
    mock_event = MagicMock()
    mock_event.data = MagicMock(content="review output")

    mock_session = MagicMock()
    mock_session.send_and_wait = AsyncMock(return_value=mock_event)
    mock_session.abort = AsyncMock()
    mock_session.destroy = AsyncMock()
    mock_session.on = MagicMock(return_value=lambda: None)

    manager = MagicMock()
    manager.list_models = AsyncMock(
        return_value=[
            MagicMock(
                id="claude-sonnet-4-6",
                name="Claude Sonnet 4.6",
                capabilities=MagicMock(
                    to_dict=lambda: {
                        "supports": {"vision": True, "reasoningEffort": False},
                        "limits": {"maxPromptTokens": 200000},
                    }
                ),
                policy=None,
                billing=None,
            )
        ]
    )
    manager.create_session = AsyncMock(return_value=mock_session)
    manager._client = object()
    return manager


@pytest.fixture
def app(mock_session_manager):
    application = create_app()
    application.state.session_manager = mock_session_manager
    application.state.event_bus = EventBus()
    application.state.review_store = ReviewStore()
    return application


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def frontend_dist(tmp_path):
    dist = tmp_path / "frontend-dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><body><div id='root'>Reviewer</div></body></html>"
    )
    (assets / "app.js").write_text("console.log('reviewer');")
    return dist


@pytest.fixture
def packaged_app(mock_session_manager, frontend_dist):
    shutdown_called = {"value": False}

    runtime = AppRuntime(
        packaged=True,
        base_url="http://127.0.0.1:8000",
        port=8000,
        frontend_dist=frontend_dist,
        shutdown_callback=lambda: shutdown_called.__setitem__("value", True),
    )
    application = create_app(runtime=runtime)
    application.state.session_manager = mock_session_manager
    application.state.event_bus = EventBus()
    application.state.review_store = ReviewStore()
    application.state.shutdown_called = shutdown_called
    return application


@pytest.fixture
def packaged_client(packaged_app):
    return TestClient(packaged_app)


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestModelsEndpoint:
    def test_list_models_returns_list(self, client):
        response = client.get("/api/models")
        assert response.status_code == 200
        data = response.json()
        assert "models" in data
        assert isinstance(data["models"], list)

    def test_list_models_includes_byok_flag(self, client):
        response = client.get("/api/models")
        data = response.json()
        assert "byok_active" in data


class TestAuthEndpoints:
    def test_auth_status_reports_runtime_snapshot(self, client):
        response = client.get("/api/auth/status")

        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
        assert data["mode"] in {"copilot_cli", "byok"}
        assert data["copilot_connected"] is True
        assert "message" in data

    def test_auth_validate_lists_models(self, client):
        response = client.post("/api/auth/validate")

        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
        assert data["models_count"] == 1
        assert data["suggested_actions"] == []

    def test_auth_validate_maps_login_guidance(self, client, app, monkeypatch):
        app.state.session_manager.list_models.side_effect = RuntimeError("please login to copilot")
        monkeypatch.setattr("backend.auth_status._detect_cli", lambda settings: True)

        response = client.post("/api/auth/validate")

        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is False
        assert "尚未登入" in data["message"]
        assert len(data["suggested_actions"]) > 0


class TestAppControlEndpoints:
    def test_app_info_reports_dev_mode(self, client):
        response = client.get("/api/app/info")
        assert response.status_code == 200
        assert response.json()["packaged"] is False
        assert response.json()["shutdown_supported"] is False

    def test_shutdown_is_unavailable_in_dev_mode(self, client):
        response = client.post("/api/app/shutdown")
        assert response.status_code == 404

    def test_pickers_are_unavailable_in_dev_mode(self, client):
        folder_response = client.post("/api/app/pick-folder")
        files_response = client.post("/api/app/pick-files")

        assert folder_response.status_code == 404
        assert files_response.status_code == 404

    def test_app_info_reports_packaged_mode(self, packaged_client):
        response = packaged_client.get("/api/app/info")
        assert response.status_code == 200
        data = response.json()
        assert data["packaged"] is True
        assert data["port"] == 8000
        assert data["shutdown_supported"] is True

    def test_shutdown_is_allowed_in_packaged_mode(self, packaged_client, packaged_app):
        response = packaged_client.post("/api/app/shutdown")
        assert response.status_code == 200
        assert response.json()["status"] == "shutting_down"
        assert packaged_app.state.shutdown_called["value"] is True

    def test_pick_folder_is_allowed_in_packaged_mode(
        self, packaged_client, monkeypatch, tmp_codebase
    ):
        monkeypatch.setattr(app_control_module, "_pick_folder_path", lambda: str(tmp_codebase))

        response = packaged_client.post("/api/app/pick-folder")

        assert response.status_code == 200
        assert response.json() == {
            "selected": True,
            "folder_path": str(tmp_codebase),
            "file_paths": None,
        }

    def test_pick_files_is_allowed_in_packaged_mode(
        self, packaged_client, monkeypatch, tmp_codebase
    ):
        selected = [str(tmp_codebase / "README.md"), str(tmp_codebase / "requirements.txt")]
        monkeypatch.setattr(app_control_module, "_pick_file_paths", lambda: selected)

        response = packaged_client.post("/api/app/pick-files")

        assert response.status_code == 200
        assert response.json() == {
            "selected": True,
            "folder_path": None,
            "file_paths": selected,
        }

    def test_frontend_dist_serves_index_and_spa_fallback(self, packaged_client):
        index_response = packaged_client.get("/")
        route_response = packaged_client.get("/reviews/latest")

        assert index_response.status_code == 200
        assert "Reviewer" in index_response.text
        assert route_response.status_code == 200
        assert "Reviewer" in route_response.text

    def test_missing_asset_still_returns_404(self, packaged_client):
        response = packaged_client.get("/assets/missing.js")
        assert response.status_code == 404

    def test_api_routes_survive_static_mount(self, packaged_client):
        response = packaged_client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestReviewEndpoint:
    def test_estimate_llm_repo_review_returns_pru_range(self, client, tmp_codebase):
        response = client.post(
            "/api/reviews/estimate",
            json={
                "source_mode": "folder",
                "folder_path": str(tmp_codebase),
                "review_profile": "llm_repo",
                "evidence_mode": "static_runtime",
                "output_mode": "structured_report",
                "gate_mode": "blocking",
                "convergence_mode": "adaptive_rerun",
                "focus_prompt": "Find drift and LLM artifacts.",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["review_profile"] == "llm_repo"
        assert data["estimated_sessions_min"] >= 7
        assert data["estimated_sessions_max"] >= data["estimated_sessions_min"]
        assert data["estimated_pru_max"] >= data["estimated_pru_min"]
        assert any(item["role"] == "challenger" for item in data["role_estimates"])

    def test_start_folder_review_returns_review_id(self, client, tmp_codebase):
        response = client.post(
            "/api/reviews",
            json={
                "source_mode": "folder",
                "folder_path": str(tmp_codebase),
                "focus_prompt": "Review this folder for architecture and reliability issues.",
                "model_preset": "balanced",
            },
        )
        assert response.status_code == 202
        data = response.json()
        assert "review_id" in data
        assert data["status"] == "started"
        assert "sse_url" in data

    def test_start_folder_review_with_invalid_path_returns_400(self, client):
        response = client.post(
            "/api/reviews",
            json={
                "source_mode": "folder",
                "folder_path": "C:\\nonexistent\\path\\that\\does\\not\\exist",
            },
        )
        assert response.status_code == 400

    def test_start_files_review_with_empty_file_paths_returns_422(self, client):
        response = client.post(
            "/api/reviews",
            json={
                "source_mode": "files",
                "file_paths": [],
            },
        )
        assert response.status_code == 422

    def test_start_folder_review_rejects_file_paths(self, client, tmp_codebase):
        response = client.post(
            "/api/reviews",
            json={
                "source_mode": "folder",
                "folder_path": str(tmp_codebase),
                "file_paths": [str(tmp_codebase / "README.md")],
            },
        )
        assert response.status_code == 422

    def test_start_review_with_model_overrides(self, client, tmp_codebase):
        response = client.post(
            "/api/reviews",
            json={
                "source_mode": "folder",
                "folder_path": str(tmp_codebase),
                "focus_prompt": "Review this codebase for architecture and maintainability issues.",
                "model_preset": "balanced",
                "model_overrides": {
                    "reviewer_1": "claude-opus-4-6",
                    "reviewer_3": "claude-haiku-4-5-20251001",
                },
            },
        )
        assert response.status_code == 202

    def test_start_files_review_returns_sse_url(self, client, tmp_codebase):
        response = client.post(
            "/api/reviews",
            json={
                "source_mode": "files",
                "file_paths": [str(tmp_codebase / "README.md")],
            },
        )
        data = response.json()
        assert data["sse_url"].startswith("/api/events/")

    def test_start_uploaded_files_review_returns_review_id(self, client):
        response = client.post(
            "/api/reviews",
            json={
                "source_mode": "uploaded_files",
                "uploaded_files": [
                    {"name": "snippet.py", "content": "print('hello')\n"},
                    {"name": "README.md", "content": "# Demo\n"},
                ],
                "focus_prompt": "Focus on correctness and report clarity.",
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert "review_id" in data
        assert data["status"] == "started"

    def test_start_uploaded_files_review_rejects_empty_payload(self, client):
        response = client.post(
            "/api/reviews",
            json={
                "source_mode": "uploaded_files",
                "uploaded_files": [],
            },
        )

        assert response.status_code == 422

    def test_start_llm_repo_review_returns_review_id(self, client, tmp_codebase):
        response = client.post(
            "/api/reviews",
            json={
                "source_mode": "folder",
                "folder_path": str(tmp_codebase),
                "review_profile": "llm_repo",
                "evidence_mode": "static_runtime",
                "output_mode": "structured_report",
                "gate_mode": "blocking",
                "convergence_mode": "adaptive_rerun",
                "focus_prompt": "Find drift and LLM artifacts.",
            },
        )

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "started"
        assert data["sse_url"].startswith("/api/events/")

    def test_get_llm_repo_review_includes_strict_fields(self, client, tmp_codebase):
        response = client.post(
            "/api/reviews",
            json={
                "source_mode": "folder",
                "folder_path": str(tmp_codebase),
                "review_profile": "llm_repo",
                "evidence_mode": "static_runtime",
                "output_mode": "structured_report",
                "gate_mode": "blocking",
                "convergence_mode": "adaptive_rerun",
            },
        )
        review_id = response.json()["review_id"]

        status_response = client.get(f"/api/reviews/{review_id}")

        assert status_response.status_code == 200
        data = status_response.json()
        assert data["review_profile"] == "llm_repo"
        assert data["gate_mode"] == "blocking"
        assert "verdict" in data
        assert "verification_summary" in data
        assert "findings" in data

    def test_get_review_includes_report_artifacts(self, client, tmp_codebase):
        response = client.post(
            "/api/reviews",
            json={
                "source_mode": "folder",
                "folder_path": str(tmp_codebase),
                "review_profile": "llm_repo",
                "evidence_mode": "static_runtime",
                "output_mode": "structured_report",
                "gate_mode": "blocking",
                "convergence_mode": "adaptive_rerun",
                "focus_prompt": "請用繁體中文整理結果。",
            },
        )
        review_id = response.json()["review_id"]

        status_response = client.get(f"/api/reviews/{review_id}")

        assert status_response.status_code == 200
        data = status_response.json()
        assert isinstance(data["session_reports"], list)
        assert len(data["session_reports"]) >= 1
        assert data["final_summary_markdown"].startswith("# 最終統整文件")
        assert data["next_steps_markdown"].startswith("# 建議下一步操作")
        assert data["artifact_summary"]["session_report_count"] >= 1

    def test_list_reviews_returns_artifact_metadata_only(self, client, tmp_codebase):
        client.post(
            "/api/reviews",
            json={
                "source_mode": "folder",
                "folder_path": str(tmp_codebase),
                "focus_prompt": "Focus on architecture and report clarity.",
            },
        )

        response = client.get("/api/reviews")

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        review = data[0]
        assert "artifact_summary" in review
        assert review["final_summary_markdown"] is None
        assert review["next_steps_markdown"] is None
        assert isinstance(review["session_reports"], list)
        if review["session_reports"]:
            assert review["session_reports"][0]["report_markdown"] is None
