"""Auth/runtime status endpoints for the local reviewer shell."""

from dataclasses import asdict

from fastapi import APIRouter, Depends

from backend.api.dependencies import get_session_manager
from backend.api.schemas import AuthStatusResponse
from backend.auth_status import collect_auth_status
from backend.config import get_settings
from backend.orchestration.session_manager import SessionManager

router = APIRouter()


@router.get("/auth/status", response_model=AuthStatusResponse)
async def auth_status(
    session_manager: SessionManager = Depends(get_session_manager),
) -> AuthStatusResponse:
    settings = get_settings()
    status = await collect_auth_status(settings, session_manager, validate=False)
    return AuthStatusResponse(**asdict(status))


@router.post("/auth/validate", response_model=AuthStatusResponse)
async def validate_auth(
    session_manager: SessionManager = Depends(get_session_manager),
) -> AuthStatusResponse:
    settings = get_settings()
    status = await collect_auth_status(settings, session_manager, validate=True)
    return AuthStatusResponse(**asdict(status))
