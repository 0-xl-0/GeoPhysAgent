from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


TaskStatus = Literal[
    "created",
    "inspecting_input",
    "planning",
    "ready",
    "executing",
    "validating",
    "waiting_for_user",
    "recovering",
    "completed",
    "completed_with_warnings",
    "incomplete",
    "failed",
    "cancelled",
]
ExecutionStatus = Literal["not_started", "running", "completed", "failed", "cancelled"]
AttemptStatus = Literal[
    "running",
    "completed",
    "failed",
    "rejected",
    "cancelled",
    "superseded",
]
EvidenceKind = Literal["tool_result", "validation", "artifact", "fact"]
EvidenceStatus = Literal["satisfied", "missing", "invalid"]
StepKind = Literal["tool", "agent", "artifact"]
AgentRunStatus = Literal["running", "completed", "incomplete", "failed", "cancelled"]
ValidatorKind = Literal["data", "semantic", "process", "result"]
ContextTrust = Literal[
    "system_policy",
    "user_input",
    "harness_verified",
    "provider_declared",
    "model_generated",
    "mixed_untrusted",
]


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    request: str
    workflow: str
    input_asset_ids: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    required_validators: tuple[str, ...] = (
        "core.data.validation_evidence",
        "core.process.required_evidence",
        "core.result.artifact_integrity",
        "core.result.final_artifact_evidence",
    )
    required_final_artifact_roles: tuple[str, ...] = ()
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExpectedEvidence:
    evidence_id: str
    kind: EvidenceKind
    source_step_id: str
    selector: str = ""
    description: str = ""
    required: bool = True
    artifact_role: str = "output"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    tool_name: str
    arguments: dict[str, Any]
    depends_on: tuple[str, ...] = ()
    expected_evidence_ids: tuple[str, ...] = ()
    optional: bool = False
    max_attempts: int = 1
    step_kind: StepKind = "tool"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionPlan:
    plan_id: str
    task_id: str
    workflow: str
    workflow_version: str
    objectives: tuple[str, ...]
    steps: tuple[PlanStep, ...]
    expected_evidence: tuple[ExpectedEvidence, ...]
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    kind: EvidenceKind
    source_step_id: str
    status: EvidenceStatus
    selector: str
    value_sha256: str | None = None
    value: Any = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    relative_path: str
    media_type: str
    role: str
    size_bytes: int
    sha256: str
    created_by_step: str
    evidence_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactManifest:
    task_id: str
    artifacts: tuple[ArtifactRecord, ...]
    snapshot_revision: int = 0
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StepAttempt:
    attempt_id: str
    step_id: str
    attempt_number: int
    tool_name: str
    provider: str | None
    status: AttemptStatus
    started_at: str
    finished_at: str | None = None
    tool_status: str | None = None
    result_sha256: str | None = None
    evidence_ids: tuple[str, ...] = ()
    error: dict[str, Any] | None = None
    provider_version: str | None = None
    tool_version: str | None = None
    tool_schema_version: str | None = None
    server_id: str | None = None
    server_version: str | None = None
    protocol_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentRunRecord:
    agent_run_id: str
    step_id: str
    runtime: str
    provider: str
    model: str
    status: AgentRunStatus
    started_at: str
    finished_at: str | None = None
    turns: int = 0
    protocol: str = "unknown"
    input_tokens: int = 0
    output_tokens: int = 0
    model_latency_ms: int = 0
    model_retry_count: int = 0
    estimated_cost_usd: float | None = None
    response_ids: tuple[str, ...] = ()
    tool_attempt_ids: tuple[str, ...] = ()
    final_artifact_id: str | None = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationExecutionRecord:
    validator_id: str
    kind: ValidatorKind
    status: str
    started_at: str
    finished_at: str
    report: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextBudget:
    """Deterministic limits applied before every model request."""

    max_total_tokens: int = 24_000
    max_system_tokens: int = 4_000
    max_tool_definition_tokens: int = 5_000
    max_history_tokens: int = 14_000
    max_tool_result_bytes: int = 32 * 1024
    recent_message_units: int = 4
    compaction_trigger_ratio: float = 0.80
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        integer_limits = (
            self.max_total_tokens,
            self.max_system_tokens,
            self.max_tool_definition_tokens,
            self.max_history_tokens,
            self.max_tool_result_bytes,
            self.recent_message_units,
        )
        if any(value <= 0 for value in integer_limits):
            raise ValueError("Context budget limits must be positive")
        if not 0.1 <= self.compaction_trigger_ratio <= 1.0:
            raise ValueError("compaction_trigger_ratio must be between 0.1 and 1.0")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextBlock:
    block_id: str
    kind: str
    source: str
    trust: ContextTrust
    content_sha256: str
    estimated_tokens: int
    references: tuple[str, ...] = ()
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CompactionRecord:
    compaction_id: str
    boundary_turn: int
    source_message_count: int
    retained_message_count: int
    dropped_message_count: int
    estimated_tokens_before: int
    estimated_tokens_after: int
    summary_sha256: str
    evidence_ids: tuple[str, ...] = ()
    tool_attempt_ids: tuple[str, ...] = ()
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextSnapshot:
    snapshot_id: str
    agent_run_id: str
    turn: int
    policy_version: str
    blocks: tuple[ContextBlock, ...]
    active_tools: tuple[str, ...]
    provider_versions: tuple[str, ...]
    message_count: int
    estimated_tokens: int
    budget: ContextBudget
    evidence_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    compaction_id: str | None = None
    context_sha256: str = ""
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AgentCheckpoint:
    """Recoverable Agent state committed inside the authoritative RunRecord."""

    task_id: str
    turn: int
    messages: tuple[dict[str, Any], ...]
    approved_plan: dict[str, Any] | None = None
    approved_plan_id: str | None = None
    completed_steps: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    model_error: dict[str, Any] | None = None
    resume_policy: str = "manual_only"
    compaction_id: str | None = None
    context_snapshot_id: str | None = None
    event_cursor: int = 0
    run_revision: int = 0
    artifact_manifest_revision: int = 0
    schema_version: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceClaim:
    claim_id: str
    evidence_id: str
    selector: str
    value_sha256: str
    value: Any
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolBindingRecord:
    provider_id: str
    provider_version: str
    tool_name: str
    tool_version: str
    tool_schema_version: str
    permissions: tuple[str, ...] = ()
    network_origins: tuple[str, ...] = ()
    server_id: str | None = None
    server_version: str | None = None
    protocol_version: str | None = None
    cwd_fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunRecord:
    task_id: str
    task_status: TaskStatus
    execution_status: ExecutionStatus
    task_spec: TaskSpec
    plan: ExecutionPlan
    created_at: str
    updated_at: str
    attempts: list[StepAttempt] = field(default_factory=list)
    evidence: list[EvidenceRecord] = field(default_factory=list)
    artifacts: list[ArtifactRecord] = field(default_factory=list)
    agent_runs: list[AgentRunRecord] = field(default_factory=list)
    validations: list[ValidationExecutionRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    context_snapshots: list[ContextSnapshot] = field(default_factory=list)
    compactions: list[CompactionRecord] = field(default_factory=list)
    agent_checkpoint: AgentCheckpoint | None = None
    tool_bindings: list[ToolBindingRecord] = field(default_factory=list)
    snapshot_revision: int = 0
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
