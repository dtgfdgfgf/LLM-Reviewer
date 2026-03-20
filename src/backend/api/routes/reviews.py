"""
POST /api/reviews          — start a new review.
GET  /api/reviews          — list all known reviews (status only, no report).
GET  /api/reviews/{id}     — get full review status + report result.
"""

import shutil
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from backend.api.dependencies import get_event_bus, get_review_store, get_session_manager
from backend.api.schemas import (
    ReviewEstimateResponse,
    ReviewRequest,
    ReviewResponse,
    ReviewStatusResponse,
    RoleEstimateResponse,
)
from backend.logging_config import get_logger
from backend.orchestration.event_bus import EventBus
from backend.orchestration.model_router import ModelPreset, ModelRouter
from backend.orchestration.orchestrator import ReviewRequest as OrchestratorRequest
from backend.orchestration.orchestrator import run_review
from backend.orchestration.report_artifacts import compact_session_report
from backend.orchestration.review_estimator import estimate_review_cost
from backend.orchestration.review_store import ReviewStore
from backend.orchestration.session_manager import SessionManager
from backend.review_inputs import normalize_local_review_input

router = APIRouter()
logger = get_logger("api.reviews")


async def _run_review_with_cleanup(*, cleanup_root: str | None = None, **kwargs) -> None:
    """Run the review pipeline and delete any temporary upload workspace afterward."""
    try:
        await run_review(**kwargs)
    finally:
        if cleanup_root:
            shutil.rmtree(cleanup_root, ignore_errors=True)


async def _resolve_model_router(
    *,
    request_body: ReviewRequest,
    session_manager: SessionManager,
) -> tuple[ModelRouter, dict[str, float]]:
    preset = ModelPreset(request_body.model_preset)
    overrides = request_body.model_overrides.to_role_dict() if request_body.model_overrides else {}
    billing_by_model: dict[str, float] = {}
    available_models = None

    try:
        available_models = await session_manager.list_models()
    except Exception as exc:
        logger.warning("Failed to discover models for review request", error=str(exc))
        if preset == ModelPreset.FREE:
            raise HTTPException(
                status_code=503,
                detail="Unable to discover available models for FREE preset",
            ) from exc

    if available_models:
        for model in available_models:
            model_id = getattr(model, "id", None)
            if not model_id:
                continue
            multiplier = 1.0
            billing = getattr(model, "billing", None)
            raw_multiplier = getattr(billing, "multiplier", None) if billing else None
            if raw_multiplier is not None:
                try:
                    multiplier = float(raw_multiplier)
                except (TypeError, ValueError):
                    multiplier = 1.0
            billing_by_model[model_id] = multiplier

    model_router = ModelRouter(
        preset=preset,
        overrides=overrides,
        available_models=available_models,
    )
    if preset == ModelPreset.FREE and not model_router.has_free_models():
        raise HTTPException(
            status_code=400,
            detail="No free (0x) models are currently available for this account",
        )
    return model_router, billing_by_model


@router.post("/reviews/estimate", response_model=ReviewEstimateResponse)
async def estimate_review(
    request_body: ReviewRequest,
    session_manager: SessionManager = Depends(get_session_manager),
) -> ReviewEstimateResponse:
    """Estimate session count and premium-request usage before starting a review."""
    normalized = None
    try:
        normalized = normalize_local_review_input(
            source_mode=request_body.source_mode,
            folder_path=request_body.folder_path,
            file_paths=request_body.file_paths,
            uploaded_files=(
                [uploaded.model_dump() for uploaded in request_body.uploaded_files]
                if request_body.uploaded_files
                else None
            ),
            focus_prompt=request_body.focus_prompt,
            legacy_task=request_body.task,
        )
        model_router, billing_by_model = await _resolve_model_router(
            request_body=request_body,
            session_manager=session_manager,
        )
        estimate = estimate_review_cost(
            normalized_input=normalized,
            review_profile=request_body.review_profile,
            convergence_mode=request_body.convergence_mode,
            model_router=model_router,
            model_billing_multipliers=billing_by_model,
        )
        return ReviewEstimateResponse(
            review_profile=estimate.review_profile,
            source_mode=estimate.source_mode,
            estimated_sessions_min=estimate.estimated_sessions_min,
            estimated_sessions_max=estimate.estimated_sessions_max,
            estimated_turns_min=estimate.estimated_turns_min,
            estimated_turns_max=estimate.estimated_turns_max,
            estimated_pru_min=estimate.estimated_pru_min,
            estimated_pru_max=estimate.estimated_pru_max,
            role_estimates=[
                RoleEstimateResponse(
                    role=item.role,
                    display_name=item.display_name,
                    model=item.model,
                    billing_multiplier=item.billing_multiplier,
                    estimated_sessions_min=item.estimated_sessions_min,
                    estimated_sessions_max=item.estimated_sessions_max,
                    estimated_turns_min=item.estimated_turns_min,
                    estimated_turns_max=item.estimated_turns_max,
                    estimated_pru_min=item.estimated_pru_min,
                    estimated_pru_max=item.estimated_pru_max,
                    optional=item.optional,
                    notes=item.notes,
                )
                for item in estimate.role_estimates
            ],
            notes=estimate.notes,
        )
    except HTTPException:
        raise
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if normalized and normalized.cleanup_root:
            shutil.rmtree(normalized.cleanup_root, ignore_errors=True)


@router.post("/reviews", response_model=ReviewResponse, status_code=202)
async def start_review(
    request_body: ReviewRequest,
    background_tasks: BackgroundTasks,
    session_manager: SessionManager = Depends(get_session_manager),
    event_bus: EventBus = Depends(get_event_bus),
    review_store: ReviewStore = Depends(get_review_store),
) -> ReviewResponse:
    """
    Start a new multi-agent code review.

    Returns immediately with a review_id. Use GET /api/events/{review_id} for
    real-time SSE streaming, or poll GET /api/reviews/{review_id} for status.
    """
    try:
        normalized = normalize_local_review_input(
            source_mode=request_body.source_mode,
            folder_path=request_body.folder_path,
            file_paths=request_body.file_paths,
            uploaded_files=(
                [uploaded.model_dump() for uploaded in request_body.uploaded_files]
                if request_body.uploaded_files
                else None
            ),
            focus_prompt=request_body.focus_prompt,
            legacy_task=request_body.task,
        )
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    review_id = str(uuid.uuid4())
    logger.info(
        "Review requested",
        review_id=review_id,
        source_mode=normalized.source_mode,
        review_root=normalized.review_root,
        selected_paths=len(normalized.selected_paths),
        model_preset=request_body.model_preset,
    )

    # Register in store immediately so GET /api/reviews/{review_id} returns 200 right away
    review_store.create(
        review_id=review_id,
        review_profile=request_body.review_profile,
        evidence_mode=request_body.evidence_mode,
        output_mode=request_body.output_mode,
        gate_mode=request_body.gate_mode,
        convergence_mode=request_body.convergence_mode,
        focus_prompt=normalized.focus_prompt,
        source_mode=normalized.source_mode,
        review_root=normalized.review_root,
        selected_paths=normalized.selected_paths,
        model_preset=request_body.model_preset,
    )

    # Build model router from request params
    model_router, _ = await _resolve_model_router(
        request_body=request_body,
        session_manager=session_manager,
    )
    overrides = request_body.model_overrides.to_role_dict() if request_body.model_overrides else {}

    # Build orchestration request
    orch_request = OrchestratorRequest(
        source_mode=normalized.source_mode,
        review_root=normalized.review_root,
        selected_paths=normalized.selected_paths,
        focus_prompt=normalized.focus_prompt,
        model_preset=request_body.model_preset,
        model_overrides={k.value: v for k, v in overrides.items()},
        review_profile=request_body.review_profile,
        evidence_mode=request_body.evidence_mode,
        output_mode=request_body.output_mode,
        gate_mode=request_body.gate_mode,
        convergence_mode=request_body.convergence_mode,
    )

    # Kick off review as background task
    background_tasks.add_task(
        _run_review_with_cleanup,
        cleanup_root=normalized.cleanup_root,
        review_id=review_id,
        request=orch_request,
        event_bus=event_bus,
        session_manager=session_manager,
        model_router=model_router,
        review_store=review_store,
    )

    return ReviewResponse(
        review_id=review_id,
        status="started",
        sse_url=f"/api/events/{review_id}",
    )


@router.get("/reviews", response_model=list[ReviewStatusResponse])
async def list_reviews(
    review_store: ReviewStore = Depends(get_review_store),
) -> list[ReviewStatusResponse]:
    """
    List all known reviews (running, complete, or errored), newest first.

    The `report` field is omitted from this listing to keep responses compact.
    Fetch GET /api/reviews/{review_id} to retrieve the full report text.
    """
    return [
        ReviewStatusResponse(
            review_id=s.review_id,
            status=s.status,
            review_profile=s.review_profile,
            evidence_mode=s.evidence_mode,
            output_mode=s.output_mode,
            gate_mode=s.gate_mode,
            convergence_mode=s.convergence_mode,
            focus_prompt=s.focus_prompt,
            source_mode=s.source_mode,
            review_root=s.review_root,
            selected_paths=s.selected_paths,
            model_preset=s.model_preset,
            started_at=s.started_at,
            completed_at=s.completed_at,
            duration_ms=s.duration_ms,
            report=None,  # omitted from list; fetch individual review for full text
            error=s.error,
            verdict=s.verdict,
            findings=[],
            consensus_findings=[],
            disputed_findings=[],
            convergence_metrics=s.convergence_metrics,
            verification_summary=s.verification_summary,
            drift_summary=s.drift_summary,
            session_reports=[compact_session_report(item) for item in (s.session_reports or [])],
            final_summary_markdown=None,
            next_steps_markdown=None,
            artifact_summary=s.artifact_summary,
            sse_url=f"/api/events/{s.review_id}",
        )
        for s in review_store.list_all()
    ]


@router.get("/reviews/{review_id}", response_model=ReviewStatusResponse)
async def get_review(
    review_id: str,
    review_store: ReviewStore = Depends(get_review_store),
) -> ReviewStatusResponse:
    """
    Get the status and result of a specific review.

    - `status: "running"` — review is in progress; poll again or subscribe to SSE.
    - `status: "complete"` — `report` contains the full final report.
    - `status: "error"` — `error` contains the failure message.

    The SSE stream (`sse_url`) remains available for the process lifetime regardless
    of status, but will return an empty stream if the review has already ended.
    """
    state = review_store.get(review_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Review not found: {review_id}")

    return ReviewStatusResponse(
        review_id=state.review_id,
        status=state.status,
        review_profile=state.review_profile,
        evidence_mode=state.evidence_mode,
        output_mode=state.output_mode,
        gate_mode=state.gate_mode,
        convergence_mode=state.convergence_mode,
        focus_prompt=state.focus_prompt,
        source_mode=state.source_mode,
        review_root=state.review_root,
        selected_paths=state.selected_paths,
        model_preset=state.model_preset,
        started_at=state.started_at,
        completed_at=state.completed_at,
        duration_ms=state.duration_ms,
        report=state.report,
        error=state.error,
        verdict=state.verdict,
        findings=state.findings or [],
        consensus_findings=state.consensus_findings or [],
        disputed_findings=state.disputed_findings or [],
        convergence_metrics=state.convergence_metrics,
        verification_summary=state.verification_summary,
        drift_summary=state.drift_summary,
        session_reports=state.session_reports or [],
        final_summary_markdown=state.final_summary_markdown,
        next_steps_markdown=state.next_steps_markdown,
        artifact_summary=state.artifact_summary,
        sse_url=f"/api/events/{review_id}",
    )
