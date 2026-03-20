"""
FastAPI application entry point.

Creates the app, configures middleware, registers routes, and manages
the Copilot client lifecycle via the FastAPI lifespan context manager.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import PurePosixPath

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api.routes import auth as auth_router
from backend.api.routes import app_control as app_control_router
from backend.api.routes import models as models_router
from backend.api.routes import reviews as reviews_router
from backend.api.routes import sse as sse_router
from backend.app_runtime import AppRuntime, resolve_frontend_dist
from backend.config import Settings, get_settings
from backend.logging_config import configure_logging, get_logger
from backend.orchestration.event_bus import EventBus
from backend.orchestration.review_store import ReviewStore
from backend.orchestration.session_manager import SessionManager
from backend.sdk_compat import apply_enterprise_sdk_patches

logger = get_logger("main")


class SPAStaticFiles(StaticFiles):
    """Serve built frontend assets and fall back to index.html for SPA routes."""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        is_asset_request = "." in PurePosixPath(path).name
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if (
                exc.status_code == 404
                and path
                and not is_asset_request
                and not path.startswith("api/")
            ):
                return await super().get_response("index.html", scope)
            raise

        if response.status_code == 404 and path and not is_asset_request and not path.startswith("api/"):
            return await super().get_response("index.html", scope)
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage startup and shutdown of the Copilot client."""
    settings: Settings = app.state.settings

    configure_logging(log_level=settings.log_level, debug=settings.debug)
    apply_enterprise_sdk_patches()
    logger.info("Reviewer starting", **settings.safe_repr())

    session_manager = SessionManager(settings)
    event_bus = EventBus()
    review_store = ReviewStore()

    await session_manager.start()

    app.state.session_manager = session_manager
    app.state.event_bus = event_bus
    app.state.review_store = review_store

    logger.info("Application ready")
    yield

    logger.info("Application shutting down")
    await session_manager.stop()


def create_app(
    settings: Settings | None = None,
    runtime: AppRuntime | None = None,
) -> FastAPI:
    """
    Factory function — creates and configures the FastAPI application.

    Accepts optional settings for testing (avoids loading .env in tests).
    """
    if settings is None:
        settings = get_settings()
    if runtime is None:
        runtime = AppRuntime(frontend_dist=resolve_frontend_dist())

    app = FastAPI(
        title="Reviewer",
        description="Local AI code reviewer",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    app.state.settings = settings
    app.state.runtime = runtime

    # CORS — allow the Vite dev server and any configured origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routes — all prefixed with /api
    app.include_router(reviews_router.router, prefix="/api", tags=["reviews"])
    app.include_router(auth_router.router, prefix="/api", tags=["auth"])
    app.include_router(models_router.router, prefix="/api", tags=["models"])
    app.include_router(sse_router.router, prefix="/api", tags=["events"])
    app.include_router(app_control_router.router, prefix="/api", tags=["app"])

    frontend_dist = runtime.frontend_dist or resolve_frontend_dist()
    if frontend_dist:
        logger.info("Serving frontend static files", directory=str(frontend_dist))
        app.mount(
            "/",
            SPAStaticFiles(directory=str(frontend_dist), html=True),
            name="frontend",
        )
    else:
        logger.info("Frontend dist not found; API-only mode enabled")

    return app


# Application instance used by uvicorn
app = create_app()
