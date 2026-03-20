"""
Windows launcher for the packaged local reviewer.

Starts FastAPI in-process, opens the default browser, and keeps the process
alive until the packaged app is shut down from the UI.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn


class LauncherError(RuntimeError):
    """Raised when the local app cannot be started cleanly."""


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _base_dir() -> Path:
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _set_working_directory() -> Path:
    base_dir = _base_dir()
    os.chdir(base_dir)
    return base_dir


def _show_error_dialog(message: str) -> None:
    title = "Reviewer 啟動失敗"
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
        return
    print(f"{title}: {message}", file=sys.stderr)


def _pick_port(host: str, start: int = 8000, end: int = 8010) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise LauncherError("8000 到 8010 連續埠都被占用，無法啟動本機 Reviewer。")


def _map_startup_error(message: str) -> LauncherError:
    lowered = message.lower()
    if any(token in lowered for token in ("no such file", "not found", "enoent", "spawn")):
        return LauncherError(
            "找不到 GitHub Copilot CLI。請先確認已安裝，或在 .env 內設定 COPILOT_CLI_PATH。"
        )
    if any(token in lowered for token in ("login", "logged in", "auth", "authenticate")):
        return LauncherError("GitHub Copilot CLI 尚未登入。請先在命令列完成登入後再啟動。")
    return LauncherError(f"Copilot 服務初始化失敗：{message}")


async def _run_preflight() -> None:
    from backend.app_runtime import resolve_frontend_dist
    from backend.config import Settings
    from backend.orchestration.session_manager import SessionManager

    settings = Settings()

    partial_byok = any(
        [
            settings.byok_provider_type is not None,
            settings.byok_api_key is not None,
            settings.byok_base_url is not None,
        ]
    )
    if partial_byok and not settings.byok_active:
        raise LauncherError(
            "BYOK 設定不完整。請同時提供 BYOK_PROVIDER_TYPE 與 BYOK_API_KEY，或移除相關設定。"
        )

    if settings.copilot_cli_path and not Path(settings.copilot_cli_path).exists():
        raise LauncherError(
            f"找不到設定的 Copilot CLI：{settings.copilot_cli_path}"
        )

    frontend_dist = resolve_frontend_dist()
    if frontend_dist is None:
        raise LauncherError(
            "找不到前端靜態檔。請先執行前端 build，再重新打包 Reviewer.exe。"
        )

    manager = SessionManager(settings)
    try:
        await manager.start()
    except Exception as exc:
        raise _map_startup_error(str(exc)) from exc
    finally:
        await manager.stop()


def _wait_for_health(base_url: str, server_thread: threading.Thread, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None

    while time.time() < deadline:
        if not server_thread.is_alive():
            raise LauncherError("本機 Reviewer 啟動失敗，請檢查 Copilot CLI 或 .env 設定。")

        try:
            with urllib.request.urlopen(f"{base_url}/api/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            time.sleep(0.2)

    if last_error is not None:
        raise LauncherError(f"等待本機服務啟動逾時：{last_error}")
    raise LauncherError("等待本機服務啟動逾時。")


def main() -> int:
    _set_working_directory()

    try:
        asyncio.run(_run_preflight())

        from backend.app_runtime import AppRuntime, resolve_frontend_dist
        from backend.config import Settings
        from backend.main import create_app

        host = "127.0.0.1"
        port = _pick_port(host)
        base_url = f"http://{host}:{port}"

        runtime = AppRuntime(
            packaged=True,
            base_url=base_url,
            port=port,
            frontend_dist=resolve_frontend_dist(),
        )
        app = create_app(settings=Settings(), runtime=runtime)

        config = uvicorn.Config(
            app=app,
            host=host,
            port=port,
            log_level="info",
            access_log=False,
            use_colors=False,
            loop="asyncio",
            http="h11",
            lifespan="on",
        )
        server = uvicorn.Server(config)
        runtime.shutdown_callback = lambda: setattr(server, "should_exit", True)

        server_thread = threading.Thread(
            target=lambda: asyncio.run(server.serve()),
            name="reviewer-server",
            daemon=False,
        )
        server_thread.start()

        _wait_for_health(base_url, server_thread)
        webbrowser.open(base_url)

        while server_thread.is_alive():
            server_thread.join(timeout=0.5)
        return 0
    except KeyboardInterrupt:
        return 0
    except LauncherError as exc:
        _show_error_dialog(str(exc))
        return 1
    except Exception as exc:  # pragma: no cover - defensive fallback
        _show_error_dialog(f"未預期的啟動錯誤：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
