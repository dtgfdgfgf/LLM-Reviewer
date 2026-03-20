"""Auth/runtime status helpers for Copilot CLI and BYOK-backed review sessions."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from backend.config import Settings
from backend.orchestration.session_manager import SessionManager


@dataclass(slots=True)
class AuthStatusResult:
    mode: Literal["copilot_cli", "byok"]
    ready: bool
    byok_active: bool
    copilot_connected: bool
    copilot_cli_detected: bool
    models_count: int
    message: str
    suggested_actions: list[str] = field(default_factory=list)


def _detect_cli(settings: Settings) -> bool:
    if settings.copilot_cli_path:
        return Path(settings.copilot_cli_path).exists()
    return shutil.which("copilot") is not None


def _map_auth_failure(exc: Exception, *, mode: str, cli_detected: bool) -> tuple[str, list[str]]:
    lowered = str(exc).lower()

    if mode == "copilot_cli":
        if not cli_detected:
            return (
                "找不到 GitHub Copilot CLI。",
                [
                    "請先安裝 GitHub Copilot CLI，或在 .env 設定 COPILOT_CLI_PATH。",
                    "安裝完成後，再按一次「驗證帳號」。",
                ],
            )
        if any(token in lowered for token in ("login", "logged in", "auth", "authenticate")):
            return (
                "GitHub Copilot CLI 尚未登入。",
                [
                    "請在命令列完成 Copilot CLI 登入後再重新驗證。",
                    "若你是用 EXE，登入完成後可直接回到畫面重試。",
                ],
            )

    if mode == "byok":
        if any(token in lowered for token in ("api key", "provider", "base_url")):
            return (
                "BYOK 設定不完整或不可用。",
                [
                    "請檢查 .env 中的 BYOK_PROVIDER_TYPE / BYOK_API_KEY / BYOK_BASE_URL。",
                    "修正後重新啟動應用，再按一次「驗證帳號」。",
                ],
            )

    return (
        f"驗證失敗：{exc}",
        ["請檢查目前的 Copilot CLI 或 BYOK 設定，然後重新驗證。"],
    )


async def collect_auth_status(
    settings: Settings,
    session_manager: SessionManager,
    *,
    validate: bool,
) -> AuthStatusResult:
    """Return a user-facing status snapshot for the current auth/runtime state."""
    mode: Literal["copilot_cli", "byok"] = "byok" if settings.byok_active else "copilot_cli"
    cli_detected = _detect_cli(settings)
    connected = session_manager._client is not None

    if not validate:
        return AuthStatusResult(
            mode=mode,
            ready=connected,
            byok_active=settings.byok_active,
            copilot_connected=connected,
            copilot_cli_detected=cli_detected,
            models_count=0,
            message=(
                "Copilot runtime 已啟動，可直接進行驗證。"
                if connected
                else "Copilot runtime 尚未就緒。"
            ),
            suggested_actions=(
                []
                if connected
                else ["請先確認 app 已正常啟動，再按「驗證帳號」。"]
            ),
        )

    try:
        models = await session_manager.list_models()
    except Exception as exc:
        message, actions = _map_auth_failure(exc, mode=mode, cli_detected=cli_detected)
        return AuthStatusResult(
            mode=mode,
            ready=False,
            byok_active=settings.byok_active,
            copilot_connected=connected,
            copilot_cli_detected=cli_detected,
            models_count=0,
            message=message,
            suggested_actions=actions,
        )

    return AuthStatusResult(
        mode=mode,
        ready=True,
        byok_active=settings.byok_active,
        copilot_connected=connected,
        copilot_cli_detected=cli_detected,
        models_count=len(models),
        message="驗證成功，模型與 Copilot runtime 皆可用。",
        suggested_actions=[],
    )
