"""
In-memory store for review state.

Allows machine callers to poll GET /api/reviews/{review_id} for status and the
final report without requiring a persistent SSE connection.

Design notes:
- Plain dict + dataclass; no locking needed (asyncio is single-threaded).
- Reviews are kept for the lifetime of the process. In production a TTL eviction
  policy (or external store) would be added, but for a local tool this is fine.
- The store is updated by run_review() directly — no EventBus subscription needed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from backend.orchestration.strict_types import (
    ConvergenceMetrics,
    DriftSummary,
    EvidenceMode,
    Finding,
    GateMode,
    GateVerdict,
    OutputMode,
    ReviewProfile,
    VerificationSummary,
)
from backend.orchestration.strict_types import ConvergenceMode as StrictConvergenceMode
from backend.orchestration.report_artifacts import ArtifactSummary, SessionReport


@dataclass
class ReviewState:
    review_id: str
    status: Literal["running", "complete", "error"]
    review_profile: ReviewProfile
    evidence_mode: EvidenceMode
    output_mode: OutputMode
    gate_mode: GateMode
    convergence_mode: StrictConvergenceMode
    focus_prompt: str
    source_mode: Literal["folder", "files", "uploaded_files"]
    review_root: str
    selected_paths: list[str]
    model_preset: str
    started_at: int          # unix ms
    completed_at: int | None = None
    duration_ms: int | None = None
    report: str | None = None
    error: str | None = None
    verdict: GateVerdict | None = None
    findings: list[Finding] | None = None
    consensus_findings: list[Finding] | None = None
    disputed_findings: list[Finding] | None = None
    convergence_metrics: ConvergenceMetrics | None = None
    verification_summary: VerificationSummary | None = None
    drift_summary: DriftSummary | None = None
    session_reports: list[SessionReport] | None = None
    final_summary_markdown: str | None = None
    next_steps_markdown: str | None = None
    artifact_summary: ArtifactSummary | None = None


class ReviewStore:
    """Append-only in-memory store of review states."""

    def __init__(self) -> None:
        self._reviews: dict[str, ReviewState] = {}

    def create(
        self,
        review_id: str,
        review_profile: ReviewProfile,
        evidence_mode: EvidenceMode,
        output_mode: OutputMode,
        gate_mode: GateMode,
        convergence_mode: StrictConvergenceMode,
        focus_prompt: str,
        source_mode: Literal["folder", "files", "uploaded_files"],
        review_root: str,
        selected_paths: list[str],
        model_preset: str,
    ) -> ReviewState:
        """Register a new review as 'running'. Called before run_review() starts."""
        state = ReviewState(
            review_id=review_id,
            status="running",
            review_profile=review_profile,
            evidence_mode=evidence_mode,
            output_mode=output_mode,
            gate_mode=gate_mode,
            convergence_mode=convergence_mode,
            focus_prompt=focus_prompt,
            source_mode=source_mode,
            review_root=review_root,
            selected_paths=list(selected_paths),
            model_preset=model_preset,
            started_at=int(time.time() * 1000),
        )
        self._reviews[review_id] = state
        return state

    def get(self, review_id: str) -> ReviewState | None:
        """Return state for a review, or None if unknown."""
        return self._reviews.get(review_id)

    def list_all(self) -> list[ReviewState]:
        """Return all known reviews, newest first."""
        return sorted(
            self._reviews.values(),
            key=lambda s: s.started_at,
            reverse=True,
        )

    def set_complete(
        self,
        review_id: str,
        report: str,
        duration_ms: int,
        verdict: GateVerdict | None = None,
        findings: list[Finding] | None = None,
        consensus_findings: list[Finding] | None = None,
        disputed_findings: list[Finding] | None = None,
        convergence_metrics: ConvergenceMetrics | None = None,
        verification_summary: VerificationSummary | None = None,
        drift_summary: DriftSummary | None = None,
        session_reports: list[SessionReport] | None = None,
        final_summary_markdown: str | None = None,
        next_steps_markdown: str | None = None,
        artifact_summary: ArtifactSummary | None = None,
    ) -> None:
        """Mark a review as successfully complete."""
        if state := self._reviews.get(review_id):
            state.status = "complete"
            state.report = report
            state.duration_ms = duration_ms
            state.completed_at = int(time.time() * 1000)
            state.verdict = verdict
            state.findings = findings or []
            state.consensus_findings = consensus_findings or []
            state.disputed_findings = disputed_findings or []
            state.convergence_metrics = convergence_metrics
            state.verification_summary = verification_summary
            state.drift_summary = drift_summary
            state.session_reports = session_reports or []
            state.final_summary_markdown = final_summary_markdown
            state.next_steps_markdown = next_steps_markdown
            state.artifact_summary = artifact_summary

    def set_error(self, review_id: str, error: str) -> None:
        """Mark a review as failed."""
        if state := self._reviews.get(review_id):
            state.status = "error"
            state.error = error
            state.completed_at = int(time.time() * 1000)
