"""
GET /api/app/info      — runtime info for the local shell.
POST /api/app/shutdown — packaged-only graceful shutdown.
POST /api/app/pick-*   — packaged-only native file/folder pickers.
"""

from __future__ import annotations

import json
import subprocess
from ipaddress import ip_address

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from backend.api.dependencies import get_app_runtime
from backend.api.schemas import AppInfoResponse, AppShutdownResponse, PathPickerResponse
from backend.app_runtime import AppRuntime

router = APIRouter()


def _is_loopback_host(host: str | None) -> bool:
    if host is None:
        return False
    if host in {"localhost", "testclient"}:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _assert_packaged_loopback(request: Request, runtime: AppRuntime) -> None:
    if not runtime.packaged:
        raise HTTPException(status_code=404, detail="This endpoint is only available in packaged mode")

    host = request.client.host if request.client else None
    if not _is_loopback_host(host):
        raise HTTPException(status_code=403, detail="Packaged app endpoints must come from loopback")


def _run_powershell_picker(script: str) -> str:
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-STA",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="PowerShell is not available") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="Native picker timed out") from exc

    if result.returncode != 0:
        detail = result.stderr.strip() or "Native picker failed"
        raise HTTPException(status_code=500, detail=detail)

    return result.stdout.strip()


def _pick_folder_path() -> str | None:
    output = _run_powershell_picker(
        """
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.CheckFileExists = $false
$dialog.CheckPathExists = $true
$dialog.ValidateNames = $false
$dialog.FileName = "Select this folder"
$dialog.Filter = "Folders|*.folder"
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
  Split-Path -Path $dialog.FileName -Parent
}
"""
    )
    return output or None


def _pick_file_paths() -> list[str]:
    output = _run_powershell_picker(
        """
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Multiselect = $true
$dialog.Filter = "All files (*.*)|*.*"
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
  @($dialog.FileNames) | ConvertTo-Json -Compress
}
"""
    )
    if not output:
        return []
    parsed = json.loads(output)
    return parsed if isinstance(parsed, list) else [str(parsed)]


@router.get("/app/info", response_model=AppInfoResponse)
async def get_app_info(
    runtime: AppRuntime = Depends(get_app_runtime),
) -> AppInfoResponse:
    """Return runtime metadata for the current UI shell."""
    return AppInfoResponse(
        packaged=runtime.packaged,
        base_url=runtime.base_url,
        port=runtime.port,
        shutdown_supported=runtime.shutdown_supported,
    )


@router.post("/app/shutdown", response_model=AppShutdownResponse)
async def shutdown_app(
    request: Request,
    background_tasks: BackgroundTasks,
    runtime: AppRuntime = Depends(get_app_runtime),
) -> AppShutdownResponse:
    """
    Request a graceful shutdown for the packaged local app.

    The endpoint is intentionally unavailable in dev mode and only accepts
    loopback requests so it cannot be used as a generic remote shutdown hook.
    """
    if not runtime.shutdown_supported:
        raise HTTPException(status_code=404, detail="Shutdown is only available in packaged mode")

    _assert_packaged_loopback(request, runtime)

    background_tasks.add_task(runtime.shutdown_callback)

    return AppShutdownResponse(
        status="shutting_down",
        detail="The local backend is shutting down. You can close this browser tab manually.",
    )


@router.post("/app/pick-folder", response_model=PathPickerResponse)
async def pick_folder(
    request: Request,
    runtime: AppRuntime = Depends(get_app_runtime),
) -> PathPickerResponse:
    """Open the packaged app's native folder picker."""
    _assert_packaged_loopback(request, runtime)

    folder_path = _pick_folder_path()
    return PathPickerResponse(selected=folder_path is not None, folder_path=folder_path)


@router.post("/app/pick-files", response_model=PathPickerResponse)
async def pick_files(
    request: Request,
    runtime: AppRuntime = Depends(get_app_runtime),
) -> PathPickerResponse:
    """Open the packaged app's native multi-file picker."""
    _assert_packaged_loopback(request, runtime)

    file_paths = _pick_file_paths()
    return PathPickerResponse(selected=bool(file_paths), file_paths=file_paths or None)
