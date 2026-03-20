"""
API request/response schemas (Pydantic models).

These are the contract between the HTTP layer and the orchestration core.
Validation happens here — the orchestration layer receives clean data.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.orchestration.strict_types import (
    ConvergenceMetrics,
    EvidenceMode,
    Finding,
    GateMode,
    GateVerdict,
    OutputMode,
    ReviewProfile,
    VerificationSummary,
)
from backend.orchestration.strict_types import ConvergenceMode as StrictConvergenceMode
from backend.orchestration.strict_types import DriftSummary
from backend.orchestration.report_artifacts import ArtifactSummary, SessionReport
from backend.tools.codebase import MAX_FILE_SIZE_BYTES


class ModelOverrides(BaseModel):
    """Per-role model overrides. Any omitted role uses the preset default."""

    orchestrator: str | None = None
    reviewer_1: str | None = None
    reviewer_2: str | None = None
    reviewer_3: str | None = None
    synthesizer: str | None = None
    spec_drift: str | None = None
    architecture_integrity: str | None = None
    security_boundary: str | None = None
    runtime_operational: str | None = None
    test_integrity: str | None = None
    llm_artifact_simplification: str | None = None
    challenger: str | None = None
    judge: str | None = None

    def to_role_dict(self) -> dict:
        """Return only the roles that have overrides."""
        from backend.orchestration.model_router import AgentRole

        result = {}
        for field_name, value in self.model_dump().items():
            if value is not None:
                try:
                    result[AgentRole(field_name)] = value
                except ValueError:
                    pass
        return result


class UploadedFileInput(BaseModel):
    """A text/code file uploaded from the browser for review."""

    name: str = Field(min_length=1, max_length=260)
    content: str = Field(min_length=1, max_length=MAX_FILE_SIZE_BYTES)


class ReviewRequest(BaseModel):
    """Request body for POST /api/reviews."""

    source_mode: Literal["folder", "files", "uploaded_files"] = Field(
        description=(
            "folder=review one local folder recursively, files=review a selected file set, "
            "uploaded_files=review text/code files dropped into the UI"
        ),
    )
    folder_path: str | None = Field(
        default=None,
        description="Absolute path to the selected folder when source_mode=folder",
    )
    file_paths: list[str] | None = Field(
        default=None,
        description="Absolute paths to the selected files when source_mode=files",
    )
    uploaded_files: list["UploadedFileInput"] | None = Field(
        default=None,
        description="Text/code files uploaded from drag-drop when source_mode=uploaded_files",
    )
    focus_prompt: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
        description="Optional review focus. Empty means use the default review brief.",
    )
    task: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
        description="Legacy alias for focus_prompt.",
    )
    model_preset: Literal["balanced", "economy", "performance", "free", "auto"] = Field(
        default="balanced",
    )
    model_overrides: ModelOverrides | None = None
    review_profile: ReviewProfile = Field(default=ReviewProfile.GENERAL)
    evidence_mode: EvidenceMode = Field(default=EvidenceMode.STATIC_FIRST)
    output_mode: OutputMode = Field(default=OutputMode.REPORT)
    gate_mode: GateMode = Field(default=GateMode.ADVISORY)
    convergence_mode: StrictConvergenceMode = Field(default=StrictConvergenceMode.SINGLE_PASS)

    @model_validator(mode="after")
    def validate_source_mode(self) -> "ReviewRequest":
        if self.source_mode == "folder":
            if not self.folder_path:
                raise ValueError("folder_path is required when source_mode is 'folder'")
            if self.file_paths or self.uploaded_files:
                raise ValueError("file_paths/uploaded_files are not allowed when source_mode is 'folder'")
            return self
        if self.source_mode == "files":
            if not self.file_paths:
                raise ValueError("file_paths must be non-empty when source_mode is 'files'")
            if self.folder_path or self.uploaded_files:
                raise ValueError("folder_path/uploaded_files are not allowed when source_mode is 'files'")
            return self
        if not self.uploaded_files:
            raise ValueError("uploaded_files must be non-empty when source_mode is 'uploaded_files'")
        if self.folder_path or self.file_paths:
            raise ValueError("folder_path/file_paths are not allowed when source_mode is 'uploaded_files'")
        return self

    @field_validator("folder_path")
    @classmethod
    def validate_folder_is_absolute(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from pathlib import Path

        p = Path(v)
        if not p.is_absolute():
            raise ValueError("folder_path must be an absolute path")
        return v

    @field_validator("file_paths")
    @classmethod
    def validate_files_are_absolute(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return values
        from pathlib import Path

        for value in values:
            if not Path(value).is_absolute():
                raise ValueError("file_paths must contain absolute paths only")
        return values


class ReviewResponse(BaseModel):
    """Response for POST /api/reviews."""

    review_id: str
    status: str
    sse_url: str


class RoleEstimateResponse(BaseModel):
    """Estimated cost breakdown for one role or optional review stage."""

    role: str
    display_name: str
    model: str
    billing_multiplier: float = 1.0
    estimated_sessions_min: int = 0
    estimated_sessions_max: int = 0
    estimated_turns_min: int = 0
    estimated_turns_max: int = 0
    estimated_pru_min: float = 0.0
    estimated_pru_max: float = 0.0
    optional: bool = False
    notes: list[str] = Field(default_factory=list)


class ReviewEstimateResponse(BaseModel):
    """Pre-flight estimate for review sessions and premium request usage."""

    review_profile: ReviewProfile = ReviewProfile.GENERAL
    source_mode: Literal["folder", "files", "uploaded_files"]
    estimated_sessions_min: int = 0
    estimated_sessions_max: int = 0
    estimated_turns_min: int = 0
    estimated_turns_max: int = 0
    estimated_pru_min: float = 0.0
    estimated_pru_max: float = 0.0
    role_estimates: list[RoleEstimateResponse] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ModelInfoResponse(BaseModel):
    """Single model info for the models list."""

    id: str
    name: str
    capabilities: dict | None = None
    policy: dict | None = None
    billing_multiplier: float | None = None


class ModelListResponse(BaseModel):
    """Response for GET /api/models."""

    models: list[ModelInfoResponse]
    byok_active: bool


class HealthResponse(BaseModel):
    """Response for GET /api/health."""

    status: str
    copilot_connected: bool


class AuthStatusResponse(BaseModel):
    """Response for auth status/validation endpoints."""

    mode: Literal["copilot_cli", "byok"]
    ready: bool
    byok_active: bool
    copilot_connected: bool
    copilot_cli_detected: bool
    models_count: int
    message: str
    suggested_actions: list[str] = Field(default_factory=list)


class AppInfoResponse(BaseModel):
    """Response for GET /api/app/info."""

    packaged: bool
    base_url: str | None = None
    port: int | None = None
    shutdown_supported: bool


class AppShutdownResponse(BaseModel):
    """Response for POST /api/app/shutdown."""

    status: Literal["shutting_down"]
    detail: str


class PathPickerResponse(BaseModel):
    """Response for packaged-only path picker endpoints."""

    selected: bool
    folder_path: str | None = None
    file_paths: list[str] | None = None


class ReviewStatusResponse(BaseModel):
    """Response for GET /api/reviews/{review_id} and GET /api/reviews."""

    review_id: str
    status: Literal["running", "complete", "error"]
    review_profile: ReviewProfile = ReviewProfile.GENERAL
    evidence_mode: EvidenceMode = EvidenceMode.STATIC_FIRST
    output_mode: OutputMode = OutputMode.REPORT
    gate_mode: GateMode = GateMode.ADVISORY
    convergence_mode: StrictConvergenceMode = StrictConvergenceMode.SINGLE_PASS
    focus_prompt: str
    source_mode: Literal["folder", "files", "uploaded_files"]
    review_root: str
    selected_paths: list[str]
    model_preset: str
    started_at: int  # unix ms
    completed_at: Optional[int] = None  # unix ms
    duration_ms: Optional[int] = None
    report: Optional[str] = None  # populated when status == "complete"
    error: Optional[str] = None  # populated when status == "error"
    verdict: Optional[GateVerdict] = None
    findings: list[Finding] = Field(default_factory=list)
    consensus_findings: list[Finding] = Field(default_factory=list)
    disputed_findings: list[Finding] = Field(default_factory=list)
    convergence_metrics: Optional[ConvergenceMetrics] = None
    verification_summary: Optional[VerificationSummary] = None
    drift_summary: Optional[DriftSummary] = None
    session_reports: list[SessionReport] = Field(default_factory=list)
    final_summary_markdown: Optional[str] = None
    next_steps_markdown: Optional[str] = None
    artifact_summary: Optional[ArtifactSummary] = None
    sse_url: str  # convenience: always /api/events/{review_id}
