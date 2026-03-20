"""
Shared types for the strict LLM-native review pipeline.

These models are used by the orchestration layer, API schemas, and UI-facing
payloads so that the strict review path can exchange structured findings,
verification results, and verdicts consistently.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ReviewProfile(str, Enum):
    GENERAL = "general"
    LLM_REPO = "llm_repo"


class EvidenceMode(str, Enum):
    STATIC_ONLY = "static_only"
    STATIC_FIRST = "static_first"
    STATIC_RUNTIME = "static_runtime"


class OutputMode(str, Enum):
    REPORT = "report"
    STRUCTURED_REPORT = "structured_report"
    STRICT_JSON = "strict_json"


class GateMode(str, Enum):
    ADVISORY = "advisory"
    BLOCKING = "blocking"


class ConvergenceMode(str, Enum):
    SINGLE_PASS = "single_pass"
    ADAPTIVE_RERUN = "adaptive_rerun"
    FIXED_DOUBLE_PASS = "fixed_double_pass"


class FindingSeverity(str, Enum):
    BLOCKING = "blocking"
    MAJOR = "major"
    MINOR = "minor"
    SUGGESTION = "suggestion"


class DriftType(str, Enum):
    SPEC = "spec"
    ARCHITECTURE = "architecture"
    SECURITY = "security"
    RUNTIME = "runtime"
    TEST = "test"
    LLM_ARTIFACT = "llm_artifact"
    NONE = "none"


class GateVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"


class FindingKind(str, Enum):
    REVIEW = "review_finding"
    RUNTIME_FAILURE = "runtime_failure"
    COVERAGE_GAP = "coverage_gap"
    LABEL_MISMATCH = "label_mismatch"
    ENV_GAP = "env_gap"


class VerificationRole(str, Enum):
    CANONICAL = "canonical"
    SUPPLEMENTAL = "supplemental"
    EXPLORATORY = "exploratory"
    STALE_SUSPECT = "stale-suspect"


class VerificationApplicability(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    ENV_GATED = "env-gated"
    UNKNOWN = "unknown"


class EvidenceRef(BaseModel):
    kind: str = Field(description="file/spec/test/runtime/config/doc")
    path: str | None = None
    line: int | None = None
    label: str | None = None


class Finding(BaseModel):
    id: str
    category: str
    severity: FindingSeverity
    drift_type: DriftType
    kind: FindingKind = FindingKind.REVIEW
    summary: str
    claim: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    why_it_matters: str
    suggested_fix: str
    gate_impact: str
    cluster_key: str
    assumption: str | None = None
    affected_scope: str | None = None
    agent_id: str | None = None
    rationale_markdown: str | None = None


class VerificationCheckResult(BaseModel):
    name: str
    display_name: str | None = None
    status: str = Field(description="passed | failed | unavailable | skipped")
    command: str | None = None
    working_dir: str = "."
    role: VerificationRole = VerificationRole.EXPLORATORY
    applicability: VerificationApplicability = VerificationApplicability.UNKNOWN
    scope: str = "repo-wide"
    source: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    kind_hint: FindingKind = FindingKind.RUNTIME_FAILURE
    summary: str
    output_excerpt: str | None = None
    blocking: bool = True


class VerificationSummary(BaseModel):
    status: str = Field(default="pending", description="pending | complete")
    checks: list[VerificationCheckResult] = Field(default_factory=list)
    blocking_failures: list[str] = Field(default_factory=list)
    unavailable_required: list[str] = Field(default_factory=list)
    verdict_predicate: str = ""


class ConvergenceMetrics(BaseModel):
    agreement_count: int = 0
    disagreement_count: int = 0
    evidence_density: float = 0.0
    deterministic_evidence_presence: float = 0.0
    unresolved_dispute_count: int = 0
    challenged_cluster_count: int = 0


class SpecialistAssignment(BaseModel):
    agent_id: str
    role: str
    display_name: str
    shared_core_files: list[str] = Field(default_factory=list)
    artifact_files: list[str] = Field(default_factory=list)
    role_extra_files: list[str] = Field(default_factory=list)
    focus: str
    risk_hypotheses: list[str] = Field(default_factory=list)
    estimated_tokens: int = 0
    shard_index: int = 1
    shard_count: int = 1


class LLMReviewPlan(BaseModel):
    shared_core_files: list[str] = Field(default_factory=list)
    artifact_files: list[str] = Field(default_factory=list)
    assignments: list[SpecialistAssignment] = Field(default_factory=list)
    risk_hypotheses: list[str] = Field(default_factory=list)


class FindingSubmission(BaseModel):
    agent_id: str
    audited_dimensions: list[str] = Field(default_factory=list)
    no_issue_sections: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    rationale_markdown: str = ""


class ChallengeDecision(BaseModel):
    cluster_key: str
    disposition: str = Field(description="uphold | downgrade | reject | needs_human_review")
    reason: str
    recommended_severity: FindingSeverity | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ChallengeSubmission(BaseModel):
    decisions: list[ChallengeDecision] = Field(default_factory=list)
    rationale_markdown: str = ""


class DriftSummary(BaseModel):
    top_drift_types: list[str] = Field(default_factory=list)
    summary: str = ""


class JudgmentSubmission(BaseModel):
    consensus_findings: list[Finding] = Field(default_factory=list)
    disputed_findings: list[Finding] = Field(default_factory=list)
    rejected_findings: list[Finding] = Field(default_factory=list)
    drift_summary: DriftSummary = Field(default_factory=DriftSummary)
    convergence_metrics: ConvergenceMetrics = Field(default_factory=ConvergenceMetrics)
    verdict: GateVerdict = GateVerdict.NEEDS_HUMAN_REVIEW
    final_report: str = ""
