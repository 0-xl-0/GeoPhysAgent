from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from geophysagent.assets import DataAsset, register_asset
from geophysagent.contracts import (
    AgentCheckpoint,
    AgentRunRecord,
    ArtifactRecord,
    CompactionRecord,
    ContextSnapshot,
    EvidenceRecord,
    ExecutionPlan,
    ExpectedEvidence,
    PlanStep,
    RunRecord,
    StepAttempt,
    TaskSpec,
    TaskStatus,
    ToolBindingRecord,
)
from geophysagent.providers import (
    ToolContext,
    ToolDefinition,
    ToolExecution,
    ToolProviderRegistry,
)
from geophysagent.run_store import (
    ArtifactStore,
    ArtifactStoreError,
    FileRunStore,
    RunLockError,
    RunStoreError,
)
from geophysagent.validation_runtime import ValidationCoordinator


class HarnessError(RuntimeError):
    """Base error for domain Harness policy and execution failures."""


class PlanPolicyError(HarnessError):
    """An execution plan violates the task or registry policy."""


class StateTransitionError(HarnessError):
    """A task attempted a transition that the domain state machine forbids."""


class EvidenceError(HarnessError):
    """Expected evidence is missing or cannot be registered safely."""


class CancellationRequested(HarnessError):
    """A task cancellation request was observed at a safe boundary."""


@dataclass(frozen=True)
class RecoveryPolicy:
    """Controls automatic replays without weakening each Tool's replay policy."""

    retryable_error_categories: tuple[str, ...] = (
        "ConnectionError",
        "InterruptedError",
        "OSError",
        "TimeoutError",
    )
    retry_interrupted_attempts: bool = True

    def can_retry(
        self,
        definition: ToolDefinition,
        step: PlanStep,
        attempt_number: int,
        error: Exception,
    ) -> bool:
        return (
            definition.replay_policy == "safe"
            and attempt_number < step.max_attempts
            and type(error).__name__ in self.retryable_error_categories
        )


TERMINAL_STATES = {
    "completed",
    "completed_with_warnings",
    "incomplete",
    "failed",
    "cancelled",
}

_TRANSITIONS: dict[str, set[str]] = {
    "created": {"inspecting_input", "failed", "cancelled"},
    "inspecting_input": {"planning", "failed", "cancelled"},
    "planning": {"ready", "failed", "cancelled"},
    "ready": {"executing", "waiting_for_user", "failed", "cancelled"},
    "executing": {"validating", "recovering", "failed", "cancelled"},
    "recovering": {"executing", "waiting_for_user", "failed", "cancelled"},
    "waiting_for_user": {"ready", "failed", "cancelled"},
    "validating": {
        "completed",
        "completed_with_warnings",
        "incomplete",
        "recovering",
        "waiting_for_user",
        "failed",
        "cancelled",
    },
}


class TaskStateMachine:
    def __init__(self, initial: TaskStatus = "created") -> None:
        self.state: TaskStatus = initial

    def transition(self, target: TaskStatus) -> tuple[TaskStatus, TaskStatus]:
        source = self.state
        if source in TERMINAL_STATES:
            raise StateTransitionError(f"Terminal task state '{source}' cannot transition")
        if target not in _TRANSITIONS.get(source, set()):
            raise StateTransitionError(f"Invalid task state transition: {source} -> {target}")
        self.state = target
        return source, target


class PlanPolicy:
    def validate(
        self,
        task: TaskSpec,
        plan: ExecutionPlan,
        registry: ToolProviderRegistry,
        assets: dict[str, DataAsset],
    ) -> None:
        _validate_identifier(task.task_id, "task_id")
        _validate_identifier(plan.plan_id, "plan_id")
        if plan.task_id != task.task_id:
            raise PlanPolicyError("ExecutionPlan task_id does not match TaskSpec")
        if plan.workflow != task.workflow:
            raise PlanPolicyError("ExecutionPlan workflow does not match TaskSpec")
        if task.schema_version != "1.0" or plan.schema_version != "1.0":
            raise PlanPolicyError("Unsupported TaskSpec or ExecutionPlan schema_version")
        if not task.request.strip():
            raise PlanPolicyError("TaskSpec request cannot be empty")
        if not plan.steps:
            raise PlanPolicyError("ExecutionPlan requires at least one step")
        if len(task.input_asset_ids) != len(set(task.input_asset_ids)):
            raise PlanPolicyError("TaskSpec input_asset_ids must be unique")
        if len(task.allowed_tools) != len(set(task.allowed_tools)):
            raise PlanPolicyError("TaskSpec allowed_tools must be unique")
        if len(task.required_validators) != len(set(task.required_validators)):
            raise PlanPolicyError("TaskSpec required_validators must be unique")
        if len(task.required_final_artifact_roles) != len(set(task.required_final_artifact_roles)):
            raise PlanPolicyError("TaskSpec required_final_artifact_roles must be unique")
        missing_assets = [name for name in task.input_asset_ids if name not in assets]
        if missing_assets:
            raise PlanPolicyError(
                f"TaskSpec references unregistered assets: {', '.join(missing_assets)}"
            )

        step_ids = [step.step_id for step in plan.steps]
        if len(step_ids) != len(set(step_ids)):
            raise PlanPolicyError("ExecutionPlan step_id values must be unique")
        step_set = set(step_ids)
        for step in plan.steps:
            _validate_identifier(step.step_id, "step_id")
            if step.step_kind not in {"tool", "agent", "artifact"}:
                raise PlanPolicyError(
                    f"Step {step.step_id} has unsupported step_kind: {step.step_kind}"
                )
            definition = None
            if step.step_kind == "tool":
                if step.tool_name not in task.allowed_tools:
                    raise PlanPolicyError(
                        f"Step {step.step_id} references a tool outside TaskSpec allowlist: "
                        f"{step.tool_name}"
                    )
                definition = registry.definition(step.tool_name)
            unknown_dependencies = set(step.depends_on) - step_set
            if unknown_dependencies:
                raise PlanPolicyError(
                    f"Step {step.step_id} has unknown dependencies: "
                    f"{', '.join(sorted(unknown_dependencies))}"
                )
            if step.step_id in step.depends_on:
                raise PlanPolicyError(f"Step {step.step_id} cannot depend on itself")
            if step.max_attempts < 1:
                raise PlanPolicyError(f"Step {step.step_id} max_attempts must be positive")
            if (
                step.step_kind == "tool"
                and step.max_attempts > 1
                and definition is not None
                and definition.replay_policy != "safe"
            ):
                raise PlanPolicyError(
                    f"Step {step.step_id} requests retries for non-replayable tool {step.tool_name}"
                )
            if step.step_kind != "tool" and step.max_attempts != 1:
                raise PlanPolicyError(
                    f"Non-tool step {step.step_id} cannot request automatic attempts"
                )
        _topological_steps(plan.steps)

        evidence_ids = [item.evidence_id for item in plan.expected_evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise PlanPolicyError("ExpectedEvidence ids must be unique")
        evidence_by_id = {item.evidence_id: item for item in plan.expected_evidence}
        for item in plan.expected_evidence:
            _validate_identifier(item.evidence_id, "evidence_id")
            if item.source_step_id not in step_set:
                raise PlanPolicyError(
                    f"Evidence {item.evidence_id} references unknown step {item.source_step_id}"
                )
            if item.selector and not item.selector.startswith("/"):
                raise PlanPolicyError(
                    f"Evidence {item.evidence_id} selector must be a JSON Pointer"
                )
        for step in plan.steps:
            for evidence_id in step.expected_evidence_ids:
                item = evidence_by_id.get(evidence_id)
                if item is None:
                    raise PlanPolicyError(
                        f"Step {step.step_id} references unknown evidence {evidence_id}"
                    )
                if item.source_step_id != step.step_id:
                    raise PlanPolicyError(
                        f"Evidence {evidence_id} belongs to {item.source_step_id}, not {step.step_id}"
                    )
        assigned = {
            evidence_id for step in plan.steps for evidence_id in step.expected_evidence_ids
        }
        unassigned = set(evidence_ids) - assigned
        if unassigned:
            raise PlanPolicyError(
                f"Expected evidence is not assigned to its source step: "
                f"{', '.join(sorted(unassigned))}"
            )


class HarnessRunResult:
    def __init__(
        self,
        record: RunRecord,
        executions: dict[str, ToolExecution],
        output_dir: Path,
    ) -> None:
        self.record = record
        self.executions = executions
        self.output_dir = output_dir


class GeoDomainHarness:
    """Executes an approved deterministic plan through the Tool Provider boundary."""

    def __init__(
        self,
        registry: ToolProviderRegistry,
        *,
        own_registry: bool = True,
        plan_policy: PlanPolicy | None = None,
        recovery_policy: RecoveryPolicy | None = None,
        validation_coordinator: ValidationCoordinator | None = None,
        fault_hook: Callable[[str, RunRecord], None] | None = None,
    ) -> None:
        self._provider_owner = registry if own_registry else None
        self._pending_provider_events = registry.drain_events() if own_registry else ()
        self.registry = registry.freeze()
        self._last_store: FileRunStore | None = None
        self._last_record: RunRecord | None = None
        self.plan_policy = plan_policy or PlanPolicy()
        self.recovery_policy = recovery_policy or RecoveryPolicy()
        self.validation_coordinator = (
            validation_coordinator or ValidationCoordinator.with_core_validators()
        )
        self.fault_hook = fault_hook

    def execute(
        self,
        *,
        task: TaskSpec,
        plan: ExecutionPlan,
        assets: dict[str, DataAsset],
        output_dir: str | Path,
    ) -> HarnessRunResult:
        try:
            return self._execute(task=task, plan=plan, assets=assets, output_dir=output_dir)
        finally:
            self.close()

    def _execute(
        self,
        *,
        task: TaskSpec,
        plan: ExecutionPlan,
        assets: dict[str, DataAsset],
        output_dir: str | Path,
    ) -> HarnessRunResult:
        output = Path(output_dir).resolve()
        self.plan_policy.validate(task, plan, self.registry, assets)
        store = FileRunStore(output)
        with store.lock():
            now = _now()
            record = RunRecord(
                task_id=task.task_id,
                task_status="created",
                execution_status="not_started",
                task_spec=task,
                plan=plan,
                created_at=now,
                updated_at=now,
                tool_bindings=[
                    ToolBindingRecord(**item.to_dict())
                    for item in self.registry.binding_snapshot(task.allowed_tools)
                ],
            )
            store.initialize(task_spec=task, plan=plan, assets=assets, record=record)
            self._last_store = store
            self._last_record = record
            machine = TaskStateMachine()
            self._fault("after_state_created", record)
            self._event(
                store,
                record,
                "task_created",
                {"workflow": task.workflow, "plan_id": plan.plan_id},
            )
            self._record_provider_events(store, record, self._pending_provider_events)
            self._pending_provider_events = ()
            for asset_id in task.input_asset_ids:
                asset = assets[asset_id]
                self._event(
                    store,
                    record,
                    "asset_registered",
                    {
                        "asset_id": asset.asset_id,
                        "format": asset.format,
                        "data_type": asset.data_type,
                        "sha256": asset.sha256,
                    },
                )
            self._transition(machine, record, store, "inspecting_input")
            self._transition(machine, record, store, "planning")
            self._transition(machine, record, store, "ready")
            self._transition(machine, record, store, "executing")
            return self._continue(
                record=record,
                machine=machine,
                store=store,
                assets=assets,
                output=output,
            )

    def resume(self, output_dir: str | Path) -> HarnessRunResult:
        try:
            return self._resume(output_dir)
        finally:
            self.close()

    def _resume(self, output_dir: str | Path) -> HarnessRunResult:
        output = Path(output_dir).resolve()
        store = FileRunStore(output)
        with store.lock():
            record = store.load()
            assert record is not None
            self._last_store = store
            self._last_record = record
            self._record_provider_events(store, record, self._pending_provider_events)
            self._pending_provider_events = ()
            if record.task_status in TERMINAL_STATES:
                raise StateTransitionError(
                    f"Terminal task state '{record.task_status}' cannot be resumed"
                )
            task, plan = store.load_task_plan()
            assets = self._load_and_verify_assets(store)
            self.plan_policy.validate(task, plan, self.registry, assets)
            current_bindings = [
                ToolBindingRecord(**item.to_dict())
                for item in self.registry.binding_snapshot(task.allowed_tools)
            ]
            if record.tool_bindings and record.tool_bindings != current_bindings:
                raise RunStoreError(
                    "Active Tool/Provider bindings differ from the approved run snapshot"
                )
            if not record.tool_bindings:
                record.tool_bindings = current_bindings
            if (
                task.to_dict() != record.task_spec.to_dict()
                or plan.to_dict() != record.plan.to_dict()
            ):
                raise RunStoreError("Committed TaskSpec/ExecutionPlan differs from RunRecord")
            machine = TaskStateMachine(record.task_status)
            if store.cancel_requested():
                self._cancel_running(record)
                self._transition(machine, record, store, "cancelled")
                store.clear_cancel_request()
                return HarnessRunResult(record, {}, output)
            self._prepare_resume(record, machine, store)
            if machine.state == "waiting_for_user":
                return HarnessRunResult(record, {}, output)
            return self._continue(
                record=record,
                machine=machine,
                store=store,
                assets=assets,
                output=output,
            )

    def close(self) -> None:
        owner = self._provider_owner
        if owner is None:
            return
        self._provider_owner = None
        owner.dispose()
        if self._last_store is not None and self._last_record is not None:
            self._record_provider_events(
                self._last_store,
                self._last_record,
                owner.drain_events(),
            )

    def _record_provider_events(
        self,
        store: FileRunStore,
        record: RunRecord,
        events: tuple[Any, ...],
    ) -> None:
        for item in events:
            self._event(
                store,
                record,
                f"provider_{item.state}",
                {"provider": item.provider_id, **item.data},
            )

    def _continue(
        self,
        *,
        record: RunRecord,
        machine: TaskStateMachine,
        store: FileRunStore,
        assets: dict[str, DataAsset],
        output: Path,
    ) -> HarnessRunResult:
        artifacts = ArtifactStore(output)
        executions: dict[str, ToolExecution] = {}
        context = ToolContext(assets, output, store.cancel_requested)
        try:
            expected = {item.evidence_id: item for item in record.plan.expected_evidence}
            if machine.state != "validating":
                for step in _topological_steps(record.plan.steps):
                    if step.step_kind != "tool":
                        self._transition(machine, record, store, "recovering")
                        self._transition(machine, record, store, "waiting_for_user")
                        record.warnings.append(
                            f"Step {step.step_id} requires its {step.step_kind} runtime and "
                            "cannot be replayed automatically"
                        )
                        self._persist(record, store)
                        return HarnessRunResult(record, executions, output)
                    if self._completed_step_is_intact(step, record, artifacts):
                        self._event(
                            store,
                            record,
                            "step_reused",
                            {
                                "step_id": step.step_id,
                                "reason": "completed evidence and artifacts verified",
                            },
                        )
                        continue
                    self._remove_step_outputs(step, record)
                    execution = self._execute_step(step, context, record, store, machine)
                    if execution is None:
                        continue
                    executions[step.step_id] = execution
                    if execution.result.status == "failed":
                        if step.optional:
                            record.warnings.append(
                                f"Optional step {step.step_id} returned failed status"
                            )
                            continue
                        raise HarnessError(
                            f"Required step {step.step_id} returned failed tool status"
                        )
                    if execution.result.status == "warning":
                        record.warnings.extend(
                            execution.result.warnings
                            or (f"Step {step.step_id} completed with warnings",)
                        )
                    records = self._collect_evidence(
                        step,
                        [expected[name] for name in step.expected_evidence_ids],
                        execution,
                        artifacts,
                        record,
                        store,
                    )
                    record.evidence.extend(records)
                    attempt_index = next(
                        index
                        for index in range(len(record.attempts) - 1, -1, -1)
                        if record.attempts[index].step_id == step.step_id
                    )
                    record.attempts[attempt_index] = replace(
                        record.attempts[attempt_index],
                        evidence_ids=tuple(item.evidence_id for item in records),
                    )
                    self._persist(record, store)
            if machine.state != "validating":
                self._transition(machine, record, store, "validating")
            self._finalize_validation(machine, record, store, output)
        except CancellationRequested:
            self._cancel_running(record)
            if machine.state not in TERMINAL_STATES:
                self._transition(machine, record, store, "cancelled")
        except Exception as exc:
            record.errors.append(_serialize_error(exc))
            if machine.state not in TERMINAL_STATES:
                self._transition(machine, record, store, "failed")
            raise
        return HarnessRunResult(record, executions, output)

    def _execute_step(
        self,
        step: PlanStep,
        context: ToolContext,
        record: RunRecord,
        store: FileRunStore,
        machine: TaskStateMachine,
    ) -> ToolExecution | None:
        last_error: Exception | None = None
        provider_id = self.registry.provider_id_for(step.tool_name)
        definition = self.registry.definition(step.tool_name)
        binding = self.registry.binding_for(step.tool_name)
        completed_attempts = sum(1 for item in record.attempts if item.step_id == step.step_id)
        for attempt_number in range(completed_attempts + 1, step.max_attempts + 1):
            if store.cancel_requested():
                raise CancellationRequested("Task cancellation was requested")
            started = _now()
            attempt = StepAttempt(
                attempt_id=str(uuid.uuid4()),
                step_id=step.step_id,
                attempt_number=attempt_number,
                tool_name=step.tool_name,
                provider=provider_id,
                status="running",
                started_at=started,
                provider_version=binding.provider_version,
                tool_version=binding.tool_version,
                tool_schema_version=binding.tool_schema_version,
                server_id=binding.server_id,
                server_version=binding.server_version,
                protocol_version=binding.protocol_version,
            )
            record.attempts.append(attempt)
            attempt_index = len(record.attempts) - 1
            self._event(
                store,
                record,
                "step_started",
                {"step_id": step.step_id, "attempt_id": attempt.attempt_id},
            )
            self._persist(record, store)
            self._fault("after_attempt_started", record)
            try:
                execution = self.registry.invoke(step.tool_name, step.arguments, context)
                digest = _json_sha256(execution.result.to_dict())
                record.attempts[attempt_index] = replace(
                    attempt,
                    status="completed",
                    finished_at=_now(),
                    tool_status=execution.result.status,
                    result_sha256=digest,
                )
                self._event(
                    store,
                    record,
                    "tool_completed",
                    {
                        "step_id": step.step_id,
                        "attempt_id": attempt.attempt_id,
                        "tool": step.tool_name,
                        "provider": provider_id,
                        "result_sha256": digest,
                    },
                )
                self._persist(record, store)
                self._fault("after_tool_completed", record)
                return execution
            except Exception as exc:
                last_error = exc
                record.attempts[attempt_index] = replace(
                    attempt,
                    status="failed",
                    finished_at=_now(),
                    error=_serialize_error(exc),
                )
                self._event(
                    store,
                    record,
                    "tool_failed",
                    {
                        "step_id": step.step_id,
                        "attempt_id": attempt.attempt_id,
                        "category": type(exc).__name__,
                    },
                )
                self._persist(record, store)
                if self.recovery_policy.can_retry(definition, step, attempt_number, exc):
                    self._transition(machine, record, store, "recovering")
                    self._event(
                        store,
                        record,
                        "recovery_started",
                        {
                            "step_id": step.step_id,
                            "attempt_number": attempt_number,
                            "category": type(exc).__name__,
                        },
                    )
                    self._transition(machine, record, store, "executing")
                    continue
                break
        if step.optional:
            record.warnings.append(f"Optional step {step.step_id} failed: {last_error}")
            return None
        if last_error is None:
            raise HarnessError(
                f"Step {step.step_id} cannot run because its attempt budget "
                f"({step.max_attempts}) is exhausted"
            )
        raise last_error

    def _completed_step_is_intact(
        self,
        step: PlanStep,
        record: RunRecord,
        artifact_store: ArtifactStore,
    ) -> bool:
        attempts = [item for item in record.attempts if item.step_id == step.step_id]
        if not attempts or attempts[-1].status != "completed":
            return False
        observed = {item.evidence_id: item for item in record.evidence}
        if any(
            evidence_id not in observed or observed[evidence_id].status != "satisfied"
            for evidence_id in step.expected_evidence_ids
        ):
            return False
        step_artifacts = [item for item in record.artifacts if item.created_by_step == step.step_id]
        try:
            for artifact in step_artifacts:
                artifact_store.verify(artifact)
        except ArtifactStoreError:
            return False
        artifact_evidence_ids = {
            item.evidence_id
            for item in record.plan.expected_evidence
            if item.source_step_id == step.step_id and item.kind == "artifact"
        }
        return all(
            any(artifact.evidence_id == evidence_id for artifact in step_artifacts)
            for evidence_id in artifact_evidence_ids
        )

    def _remove_step_outputs(self, step: PlanStep, record: RunRecord) -> None:
        previous = [item for item in record.attempts if item.step_id == step.step_id]
        if previous:
            definition = self.registry.definition(step.tool_name)
            if definition.replay_policy != "safe":
                raise HarnessError(
                    f"Step {step.step_id} has prior execution state and Tool "
                    f"{step.tool_name} is not safe to replay"
                )
            if len(previous) >= step.max_attempts:
                raise HarnessError(
                    f"Step {step.step_id} needs replay but its attempt budget "
                    f"({step.max_attempts}) is exhausted"
                )
            for index in range(len(record.attempts) - 1, -1, -1):
                attempt = record.attempts[index]
                if attempt.step_id == step.step_id and attempt.status == "completed":
                    record.attempts[index] = replace(
                        attempt,
                        status="superseded",
                        error={
                            "category": "InvalidatedCheckpoint",
                            "message": "Completed output evidence or artifact was not intact during recovery",
                            "retryable": True,
                        },
                    )
                    break
        evidence_ids = set(step.expected_evidence_ids)
        record.evidence[:] = [
            item for item in record.evidence if item.evidence_id not in evidence_ids
        ]
        record.artifacts[:] = [
            item for item in record.artifacts if item.created_by_step != step.step_id
        ]
        record.validations.clear()

    def _finalize_validation(
        self,
        machine: TaskStateMachine,
        record: RunRecord,
        store: FileRunStore,
        output: Path,
    ) -> None:
        if store.cancel_requested():
            raise CancellationRequested("Task cancellation was requested")
        self._fault("before_validation", record)
        observed = {item.evidence_id: item for item in record.evidence}
        missing = [
            item.evidence_id
            for item in record.plan.expected_evidence
            if item.required
            and (
                item.evidence_id not in observed or observed[item.evidence_id].status != "satisfied"
            )
        ]
        if missing and not any(
            error.get("category") == "MissingEvidence" for error in record.errors
        ):
            record.errors.append(
                {
                    "category": "MissingEvidence",
                    "message": f"Required evidence not satisfied: {', '.join(missing)}",
                    "retryable": False,
                }
            )
        coordinated = self.validation_coordinator.run(record, output)
        record.validations[:] = list(coordinated.executions)
        for validation in coordinated.executions:
            self._event(
                store,
                record,
                "validation_completed",
                {
                    "validator_id": validation.validator_id,
                    "kind": validation.kind,
                    "status": validation.status,
                },
            )
        self._persist(record, store)
        self._fault("after_validation", record)
        if coordinated.status == "fail":
            if not missing:
                record.errors.append(
                    {
                        "category": "ValidationFailed",
                        "message": "One or more required validators failed",
                        "retryable": False,
                    }
                )
            self._transition(machine, record, store, "incomplete")
        else:
            if coordinated.status == "warning":
                record.warnings.append("One or more required validators reported warnings")
            self._transition(
                machine,
                record,
                store,
                "completed_with_warnings" if record.warnings else "completed",
            )

    def _prepare_resume(
        self,
        record: RunRecord,
        machine: TaskStateMachine,
        store: FileRunStore,
    ) -> None:
        interrupted_steps: set[str] = set()
        for index, attempt in enumerate(record.attempts):
            if attempt.status == "running":
                interrupted_steps.add(attempt.step_id)
                record.attempts[index] = replace(
                    attempt,
                    status="failed",
                    finished_at=_now(),
                    error={
                        "category": "InterruptedExecution",
                        "message": "Previous process ended before the Tool Attempt committed",
                        "retryable": True,
                    },
                )
        if interrupted_steps:
            self._event(
                store,
                record,
                "interrupted_attempts_detected",
                {"step_ids": sorted(interrupted_steps)},
            )
            self._persist(record, store)

        if machine.state == "created":
            self._transition(machine, record, store, "inspecting_input")
        if machine.state == "inspecting_input":
            self._transition(machine, record, store, "planning")
        if machine.state == "planning":
            self._transition(machine, record, store, "ready")
        if machine.state == "ready":
            self._transition(machine, record, store, "executing")
        elif machine.state == "recovering":
            self._transition(machine, record, store, "executing")
        elif machine.state == "waiting_for_user":
            return

        if interrupted_steps and machine.state == "executing":
            by_id = {step.step_id: step for step in record.plan.steps}
            unsafe = []
            for step_id in interrupted_steps:
                step = by_id.get(step_id)
                if step is None or step.step_kind != "tool":
                    unsafe.append(step_id)
                    continue
                definition = self.registry.definition(step.tool_name)
                used = sum(1 for item in record.attempts if item.step_id == step_id)
                if (
                    not self.recovery_policy.retry_interrupted_attempts
                    or definition.replay_policy != "safe"
                    or used >= step.max_attempts
                ):
                    unsafe.append(step_id)
            self._transition(machine, record, store, "recovering")
            if unsafe:
                record.warnings.append(
                    "Interrupted steps require manual recovery: " + ", ".join(sorted(unsafe))
                )
                self._persist(record, store)
                self._transition(machine, record, store, "waiting_for_user")
            else:
                self._event(
                    store,
                    record,
                    "recovery_started",
                    {"step_ids": sorted(interrupted_steps), "reason": "interrupted process"},
                )
                self._transition(machine, record, store, "executing")

    def _load_and_verify_assets(self, store: FileRunStore) -> dict[str, DataAsset]:
        persisted = store.load_assets()
        verified: dict[str, DataAsset] = {}
        for asset_id, expected in persisted.items():
            current = register_asset(expected.source_path, asset_id=asset_id)
            if (
                current.sha256 != expected.sha256
                or current.size_bytes != expected.size_bytes
                or current.format != expected.format
                or current.data_type != expected.data_type
            ):
                raise RunStoreError(f"Input asset changed since the checkpoint: asset://{asset_id}")
            verified[asset_id] = current
        return verified

    def _cancel_running(self, record: RunRecord) -> None:
        for index, attempt in enumerate(record.attempts):
            if attempt.status == "running":
                record.attempts[index] = replace(
                    attempt,
                    status="cancelled",
                    finished_at=_now(),
                    error={
                        "category": "Cancelled",
                        "message": "Task cancellation was requested",
                        "retryable": False,
                    },
                )
        for index, agent_run in enumerate(record.agent_runs):
            if agent_run.status == "running":
                record.agent_runs[index] = replace(
                    agent_run,
                    status="cancelled",
                    finished_at=_now(),
                )

    def _fault(self, phase: str, record: RunRecord) -> None:
        if self.fault_hook is not None:
            self.fault_hook(phase, record)

    def _collect_evidence(
        self,
        step: PlanStep,
        expected: list[ExpectedEvidence],
        execution: ToolExecution,
        artifact_store: ArtifactStore,
        record: RunRecord,
        store: FileRunStore,
    ) -> list[EvidenceRecord]:
        collected: list[EvidenceRecord] = []
        for item in expected:
            try:
                value = _json_pointer(execution.result.data, item.selector)
                if item.kind == "artifact":
                    paths = value if isinstance(value, list) else [value]
                    if not paths or any(not isinstance(path, str) for path in paths):
                        raise EvidenceError(
                            "Artifact evidence must resolve to a path or path array"
                        )
                    registered = []
                    for index, path in enumerate(paths, start=1):
                        artifact_id = (
                            item.evidence_id
                            if len(paths) == 1
                            else f"{item.evidence_id}-{index:02d}"
                        )
                        artifact = artifact_store.register(
                            path,
                            artifact_id=artifact_id,
                            role=item.artifact_role,
                            created_by_step=step.step_id,
                            evidence_id=item.evidence_id,
                        )
                        record.artifacts.append(artifact)
                        registered.append(artifact.relative_path)
                        self._event(
                            store,
                            record,
                            "artifact_registered",
                            {
                                "artifact_id": artifact.artifact_id,
                                "relative_path": artifact.relative_path,
                                "sha256": artifact.sha256,
                            },
                        )
                    value = registered
                collected.append(
                    EvidenceRecord(
                        evidence_id=item.evidence_id,
                        kind=item.kind,
                        source_step_id=step.step_id,
                        status="satisfied",
                        selector=item.selector,
                        value_sha256=_json_sha256(value),
                        value=value,
                    )
                )
            except (EvidenceError, KeyError, IndexError, TypeError, ValueError) as exc:
                collected.append(
                    EvidenceRecord(
                        evidence_id=item.evidence_id,
                        kind=item.kind,
                        source_step_id=step.step_id,
                        status="missing",
                        selector=item.selector,
                        message=str(exc),
                    )
                )
        return collected

    def _transition(
        self,
        machine: TaskStateMachine,
        record: RunRecord,
        store: FileRunStore,
        target: TaskStatus,
    ) -> None:
        source, current = machine.transition(target)
        record.task_status = current
        if current == "executing":
            record.execution_status = "running"
        elif current in {"completed", "completed_with_warnings", "incomplete"}:
            record.execution_status = "completed"
        elif current == "failed":
            record.execution_status = "failed"
        elif current == "cancelled":
            record.execution_status = "cancelled"
        self._persist(record, store)
        self._event(
            store,
            record,
            "task_state_changed",
            {"from": source, "to": current},
        )
        self._fault(f"after_state_{current}", record)

    def _event(
        self,
        store: FileRunStore,
        record: RunRecord,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        store.append_event(
            {
                "schema_version": "1.0",
                "event_id": str(uuid.uuid4()),
                "task_id": record.task_id,
                "type": event_type,
                "at": _now(),
                "data": data,
            }
        )

    def _persist(self, record: RunRecord, store: FileRunStore) -> None:
        record.updated_at = _now()
        store.save(record)


class TaskRunSession:
    """Manual lifecycle facade for Agent/Explore runtimes using Harness authority."""

    def __init__(
        self,
        *,
        registry: ToolProviderRegistry,
        task: TaskSpec,
        plan: ExecutionPlan,
        assets: dict[str, DataAsset],
        output_dir: str | Path,
        validation_coordinator: ValidationCoordinator | None = None,
    ) -> None:
        self.output = Path(output_dir).resolve()
        self._provider_owner = registry
        pending_provider_events = registry.drain_events()
        self.registry = registry.freeze()
        self.task = task
        self.plan = plan
        self.assets = assets
        self.store = FileRunStore(self.output)
        self.harness = GeoDomainHarness(
            self.registry,
            own_registry=False,
            validation_coordinator=validation_coordinator,
        )
        self.harness.plan_policy.validate(task, plan, registry, assets)
        self._lock = self.store.lock()
        self._lock.__enter__()
        now = _now()
        self.record = RunRecord(
            task_id=task.task_id,
            task_status="created",
            execution_status="not_started",
            task_spec=task,
            plan=plan,
            created_at=now,
            updated_at=now,
            tool_bindings=[
                ToolBindingRecord(**item.to_dict())
                for item in self.registry.binding_snapshot(task.allowed_tools)
            ],
        )
        try:
            self.store.initialize(task_spec=task, plan=plan, assets=assets, record=self.record)
            self.machine = TaskStateMachine()
            self.harness._event(
                self.store,
                self.record,
                "task_created",
                {"workflow": task.workflow, "plan_id": plan.plan_id},
            )
            self._record_provider_events(pending_provider_events)
            for asset_id in task.input_asset_ids:
                asset = assets[asset_id]
                self.harness._event(
                    self.store,
                    self.record,
                    "asset_registered",
                    {
                        "asset_id": asset.asset_id,
                        "format": asset.format,
                        "data_type": asset.data_type,
                        "sha256": asset.sha256,
                    },
                )
            for state in ("inspecting_input", "planning", "ready", "executing"):
                self.harness._transition(self.machine, self.record, self.store, state)
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> "TaskRunSession":
        return self

    @classmethod
    def reopen(
        cls,
        *,
        registry: ToolProviderRegistry,
        output_dir: str | Path,
        validation_coordinator: ValidationCoordinator | None = None,
    ) -> "TaskRunSession":
        """Reopen a non-terminal Agent task at an explicit manual resume boundary."""
        self = cls.__new__(cls)
        self.output = Path(output_dir).resolve()
        self._provider_owner = registry
        pending_provider_events = registry.drain_events()
        self.registry = registry.freeze()
        self.store = FileRunStore(self.output)
        self.harness = GeoDomainHarness(
            self.registry,
            own_registry=False,
            validation_coordinator=validation_coordinator,
        )
        self._lock = self.store.lock()
        self._lock.__enter__()
        try:
            self.record = self.store.load()
            assert self.record is not None
            if self.record.task_status in TERMINAL_STATES:
                raise StateTransitionError(
                    f"Terminal task state '{self.record.task_status}' cannot be resumed"
                )
            self.task, self.plan = self.store.load_task_plan()
            self.assets = self.harness._load_and_verify_assets(self.store)
            self.harness.plan_policy.validate(self.task, self.plan, self.registry, self.assets)
            expected_bindings = [
                ToolBindingRecord(**item.to_dict())
                for item in self.registry.binding_snapshot(self.task.allowed_tools)
            ]
            if self.record.tool_bindings and self.record.tool_bindings != expected_bindings:
                raise RunStoreError(
                    "Active Tool/Provider bindings differ from the approved run snapshot"
                )
            if not self.record.tool_bindings:
                self.record.tool_bindings = expected_bindings
            self._record_provider_events(pending_provider_events)
            interrupted_agent_ids: list[str] = []
            for index, agent_run in enumerate(self.record.agent_runs):
                if agent_run.status != "running":
                    continue
                interrupted_agent_ids.append(agent_run.agent_run_id)
                self.record.agent_runs[index] = replace(
                    agent_run,
                    status="incomplete",
                    finished_at=_now(),
                    error={
                        "category": "InterruptedAgentRun",
                        "message": "Agent process ended before a committed completion boundary",
                    },
                )
                self.harness._event(
                    self.store,
                    self.record,
                    "agent_run_recovered",
                    {
                        "agent_run_id": agent_run.agent_run_id,
                        "prior_status": "running",
                        "new_status": "incomplete",
                    },
                )
            if interrupted_agent_ids:
                self.harness._persist(self.record, self.store)
            self.machine = TaskStateMachine(self.record.task_status)
            if self.machine.state == "waiting_for_user":
                self.harness._transition(self.machine, self.record, self.store, "ready")
                self.harness._transition(self.machine, self.record, self.store, "executing")
            elif self.machine.state == "recovering":
                self.harness._transition(self.machine, self.record, self.store, "executing")
            elif self.machine.state != "executing":
                raise StateTransitionError(
                    f"Agent task cannot be manually resumed from '{self.machine.state}'"
                )
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        owner = getattr(self, "_provider_owner", None)
        lock = getattr(self, "_lock", None)
        cleanup_error: BaseException | None = None
        try:
            if owner is not None:
                self._provider_owner = None
                try:
                    owner.dispose()
                except BaseException as exc:
                    cleanup_error = exc
                if hasattr(self, "store") and hasattr(self, "record"):
                    self._record_provider_events(owner.drain_events())
        finally:
            if lock is not None:
                self._lock = None
                lock.__exit__(None, None, None)
        if cleanup_error is not None:
            raise cleanup_error

    def start_agent_run(
        self,
        *,
        step_id: str,
        runtime: str,
        provider: str,
        model: str,
        protocol: str = "unknown",
    ) -> str:
        self._ensure_mutable()
        step = self._step(step_id)
        if step.step_kind != "agent":
            raise HarnessError(f"Step {step_id} is not an Agent Runtime step")
        agent_run_id = str(uuid.uuid4())
        self.record.agent_runs.append(
            AgentRunRecord(
                agent_run_id=agent_run_id,
                step_id=step_id,
                runtime=runtime,
                provider=provider,
                model=model,
                status="running",
                started_at=_now(),
                protocol=protocol,
            )
        )
        self.harness._event(
            self.store,
            self.record,
            "agent_run_started",
            {
                "agent_run_id": agent_run_id,
                "step_id": step_id,
                "runtime": runtime,
                "provider": provider,
                "model": model,
                "protocol": protocol,
            },
        )
        self.harness._persist(self.record, self.store)
        return agent_run_id

    def record_agent_turn(
        self,
        agent_run_id: str,
        turn: int,
        *,
        response_id: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: int = 0,
    ) -> None:
        index = self._agent_index(agent_run_id)
        current = self.record.agent_runs[index]
        response_ids = current.response_ids
        if response_id and response_id not in response_ids:
            response_ids = (*response_ids, response_id)
        self.record.agent_runs[index] = replace(
            current,
            turns=turn,
            input_tokens=current.input_tokens + max(0, input_tokens),
            output_tokens=current.output_tokens + max(0, output_tokens),
            model_latency_ms=current.model_latency_ms + max(0, latency_ms),
            response_ids=response_ids,
        )
        self.harness._event(
            self.store,
            self.record,
            "agent_turn_completed",
            {
                "agent_run_id": agent_run_id,
                "turn": turn,
                "response_id": response_id,
                "input_tokens": max(0, input_tokens),
                "output_tokens": max(0, output_tokens),
                "latency_ms": max(0, latency_ms),
            },
        )
        self.harness._persist(self.record, self.store)

    def record_agent_checkpoint(
        self,
        checkpoint: AgentCheckpoint,
        *,
        compaction: CompactionRecord | None = None,
        migration_source: str | None = None,
    ) -> AgentCheckpoint:
        """Commit recoverable Agent state into the authoritative RunRecord snapshot."""

        self._ensure_mutable()
        if checkpoint.task_id != self.task.task_id:
            raise HarnessError("Agent checkpoint belongs to another task")
        event_count = 1
        new_compaction = compaction is not None and not any(
            item.compaction_id == compaction.compaction_id for item in self.record.compactions
        )
        if new_compaction:
            assert compaction is not None
            self.record.compactions.append(compaction)
            event_count += 1
        if migration_source is not None:
            event_count += 1
        linked = replace(
            checkpoint,
            event_cursor=self.store.event_count() + event_count,
            run_revision=self.record.snapshot_revision + 1,
            artifact_manifest_revision=self.record.snapshot_revision + 1,
        )
        self.record.agent_checkpoint = linked
        if new_compaction:
            assert compaction is not None
            self.harness._event(
                self.store,
                self.record,
                "context_compacted",
                {
                    "compaction_id": compaction.compaction_id,
                    "boundary_turn": compaction.boundary_turn,
                    "dropped_message_count": compaction.dropped_message_count,
                    "estimated_tokens_before": compaction.estimated_tokens_before,
                    "estimated_tokens_after": compaction.estimated_tokens_after,
                },
            )
        if migration_source is not None:
            self.harness._event(
                self.store,
                self.record,
                "agent_checkpoint_migrated",
                {"source": migration_source, "target_schema_version": linked.schema_version},
            )
        self.harness._event(
            self.store,
            self.record,
            "agent_checkpoint_committed",
            {
                "turn": linked.turn,
                "approved_plan_id": linked.approved_plan_id,
                "completed_steps": list(linked.completed_steps),
                "evidence_ids": list(linked.evidence_ids),
                "compaction_id": linked.compaction_id,
                "resume_policy": linked.resume_policy,
                "run_revision": linked.run_revision,
                "event_cursor": linked.event_cursor,
            },
        )
        self.harness._persist(self.record, self.store)
        return linked

    def record_context_snapshot(self, snapshot: ContextSnapshot) -> None:
        """Persist the exact bounded-context metadata before a model call."""

        self._ensure_mutable()
        if snapshot.agent_run_id not in {item.agent_run_id for item in self.record.agent_runs}:
            raise HarnessError("Context Snapshot references an unknown Agent Run")
        if any(item.snapshot_id == snapshot.snapshot_id for item in self.record.context_snapshots):
            raise HarnessError(f"Context Snapshot already exists: {snapshot.snapshot_id}")
        self.record.context_snapshots.append(snapshot)
        checkpoint = self.record.agent_checkpoint
        if checkpoint is None:
            raise HarnessError("Context Snapshot requires an Agent checkpoint")
        next_revision = self.record.snapshot_revision + 1
        event_cursor = self.store.event_count() + 1
        self.record.agent_checkpoint = replace(
            checkpoint,
            context_snapshot_id=snapshot.snapshot_id,
            event_cursor=event_cursor,
            run_revision=next_revision,
            artifact_manifest_revision=next_revision,
        )
        self.harness._event(
            self.store,
            self.record,
            "context_snapshot_recorded",
            {
                "snapshot_id": snapshot.snapshot_id,
                "agent_run_id": snapshot.agent_run_id,
                "turn": snapshot.turn,
                "policy_version": snapshot.policy_version,
                "estimated_tokens": snapshot.estimated_tokens,
                "message_count": snapshot.message_count,
                "active_tools": list(snapshot.active_tools),
                "evidence_ids": list(snapshot.evidence_ids),
                "artifact_ids": list(snapshot.artifact_ids),
                "compaction_id": snapshot.compaction_id,
                "context_sha256": snapshot.context_sha256,
            },
        )
        self.harness._persist(self.record, self.store)

    def record_agent_retry(self, agent_run_id: str, *, category: str, retry_number: int) -> None:
        index = self._agent_index(agent_run_id)
        current = self.record.agent_runs[index]
        self.record.agent_runs[index] = replace(
            current, model_retry_count=current.model_retry_count + 1
        )
        self.harness._event(
            self.store,
            self.record,
            "model_retry_scheduled",
            {
                "agent_run_id": agent_run_id,
                "category": category,
                "retry_number": retry_number,
            },
        )
        self.harness._persist(self.record, self.store)

    def invoke_agent_tool(
        self,
        *,
        agent_run_id: str,
        step_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[ToolExecution, str]:
        self._ensure_mutable()
        if tool_name not in self.task.allowed_tools:
            raise PlanPolicyError(f"Agent requested a Tool outside TaskSpec allowlist: {tool_name}")
        if self.store.cancel_requested():
            raise CancellationRequested("Task cancellation was requested")
        provider_id = self.registry.provider_id_for(tool_name)
        binding = self._binding(tool_name)
        attempt_number = 1 + sum(
            1
            for item in self.record.attempts
            if item.step_id == step_id and item.tool_name == tool_name
        )
        attempt = StepAttempt(
            attempt_id=str(uuid.uuid4()),
            step_id=step_id,
            attempt_number=attempt_number,
            tool_name=tool_name,
            provider=provider_id,
            status="running",
            started_at=_now(),
            provider_version=binding.provider_version,
            tool_version=binding.tool_version,
            tool_schema_version=binding.tool_schema_version,
            server_id=binding.server_id,
            server_version=binding.server_version,
            protocol_version=binding.protocol_version,
        )
        self.record.attempts.append(attempt)
        attempt_index = len(self.record.attempts) - 1
        self._attach_attempt(agent_run_id, attempt.attempt_id)
        self.harness._event(
            self.store,
            self.record,
            "tool_started",
            {
                "agent_run_id": agent_run_id,
                "step_id": step_id,
                "attempt_id": attempt.attempt_id,
                "tool": tool_name,
                "provider": provider_id,
            },
        )
        self.harness._persist(self.record, self.store)
        try:
            execution = self.registry.invoke(
                tool_name,
                arguments,
                ToolContext(self.assets, self.output, self.store.cancel_requested),
            )
        except Exception as exc:
            self.record.attempts[attempt_index] = replace(
                attempt,
                status="failed",
                finished_at=_now(),
                error=_serialize_error(exc),
            )
            self.harness._event(
                self.store,
                self.record,
                "tool_failed",
                {
                    "agent_run_id": agent_run_id,
                    "attempt_id": attempt.attempt_id,
                    "category": type(exc).__name__,
                },
            )
            self.harness._persist(self.record, self.store)
            raise
        digest = _json_sha256(execution.result.to_dict())
        self.record.attempts[attempt_index] = replace(
            attempt,
            status="completed",
            finished_at=_now(),
            tool_status=execution.result.status,
            result_sha256=digest,
        )
        if execution.result.status == "warning":
            self.record.warnings.extend(
                execution.result.warnings or (f"Tool {tool_name} completed with warnings",)
            )
        self.harness._event(
            self.store,
            self.record,
            "tool_completed",
            {
                "agent_run_id": agent_run_id,
                "attempt_id": attempt.attempt_id,
                "tool": tool_name,
                "provider": provider_id,
                "result_sha256": digest,
            },
        )
        self.harness._persist(self.record, self.store)
        return execution, attempt.attempt_id

    def bind_tool_evidence(
        self,
        *,
        step_id: str,
        execution: ToolExecution,
        attempt_id: str,
        evidence_ids: tuple[str, ...],
    ) -> tuple[EvidenceRecord, ...]:
        step = self._step(step_id)
        expected = {item.evidence_id: item for item in self.plan.expected_evidence}
        selected = [expected[item] for item in evidence_ids]
        records = self.harness._collect_evidence(
            step,
            selected,
            execution,
            ArtifactStore(self.output),
            self.record,
            self.store,
        )
        self.record.evidence[:] = [
            item for item in self.record.evidence if item.evidence_id not in evidence_ids
        ]
        self.record.evidence.extend(records)
        index = next(
            index
            for index, attempt in enumerate(self.record.attempts)
            if attempt.attempt_id == attempt_id
        )
        self.record.attempts[index] = replace(
            self.record.attempts[index],
            evidence_ids=tuple(item.evidence_id for item in records),
        )
        self.harness._persist(self.record, self.store)
        return tuple(records)

    def register_planned_artifact(
        self,
        *,
        step_id: str,
        evidence_id: str,
        path: str | Path,
        artifact_id: str,
    ) -> ArtifactRecord:
        self._ensure_mutable()
        step = self._step(step_id)
        expected = next(
            (
                item
                for item in self.plan.expected_evidence
                if item.evidence_id == evidence_id
                and item.source_step_id == step_id
                and item.kind == "artifact"
            ),
            None,
        )
        if expected is None or evidence_id not in step.expected_evidence_ids:
            raise EvidenceError(
                f"Artifact evidence {evidence_id} is not assigned to step {step_id}"
            )
        artifact = ArtifactStore(self.output).register(
            path,
            artifact_id=artifact_id,
            role=expected.artifact_role,
            created_by_step=step_id,
            evidence_id=evidence_id,
        )
        if any(item.artifact_id == artifact_id for item in self.record.artifacts):
            raise EvidenceError(f"Artifact id already exists: {artifact_id}")
        self.record.artifacts.append(artifact)
        prior = next(
            (item for item in self.record.evidence if item.evidence_id == evidence_id),
            None,
        )
        if prior is None:
            value: Any = artifact.relative_path
        elif isinstance(prior.value, list):
            value = [*prior.value, artifact.relative_path]
        else:
            value = [prior.value, artifact.relative_path]
        self.record.evidence[:] = [
            item for item in self.record.evidence if item.evidence_id != evidence_id
        ]
        self.record.evidence.append(
            EvidenceRecord(
                evidence_id=evidence_id,
                kind="artifact",
                source_step_id=step_id,
                status="satisfied",
                selector=expected.selector,
                value_sha256=_json_sha256(value),
                value=value,
            )
        )
        self.harness._event(
            self.store,
            self.record,
            "artifact_registered",
            {
                "artifact_id": artifact.artifact_id,
                "relative_path": artifact.relative_path,
                "sha256": artifact.sha256,
                "role": artifact.role,
            },
        )
        self.harness._persist(self.record, self.store)
        return artifact

    def record_planned_evidence(
        self,
        *,
        step_id: str,
        evidence_id: str,
        value: Any,
    ) -> EvidenceRecord:
        self._ensure_mutable()
        step = self._step(step_id)
        expected = next(
            (
                item
                for item in self.plan.expected_evidence
                if item.evidence_id == evidence_id
                and item.source_step_id == step_id
                and item.kind != "artifact"
            ),
            None,
        )
        if expected is None or evidence_id not in step.expected_evidence_ids:
            raise EvidenceError(f"Evidence {evidence_id} is not assigned to step {step_id}")
        record = EvidenceRecord(
            evidence_id=evidence_id,
            kind=expected.kind,
            source_step_id=step_id,
            status="satisfied",
            selector=expected.selector,
            value_sha256=_json_sha256(value),
            value=value,
        )
        self.record.evidence[:] = [
            item for item in self.record.evidence if item.evidence_id != evidence_id
        ]
        self.record.evidence.append(record)
        self.harness._event(
            self.store,
            self.record,
            "evidence_recorded",
            {"evidence_id": evidence_id, "step_id": step_id, "kind": expected.kind},
        )
        self.harness._persist(self.record, self.store)
        return record

    def record_agent_evidence(
        self,
        *,
        step_id: str,
        evidence_id: str,
        kind: str,
        value: Any,
        attempt_id: str,
    ) -> EvidenceRecord:
        """Record runtime evidence while keeping the top-level plan immutable."""
        self._ensure_mutable()
        step = self._step(step_id)
        if step.step_kind != "agent":
            raise EvidenceError("Dynamic Agent evidence requires an Agent Runtime step")
        _validate_identifier(evidence_id, "evidence_id")
        if kind not in {"tool_result", "validation", "fact"}:
            raise EvidenceError(f"Unsupported dynamic Agent evidence kind: {kind}")
        if any(item.evidence_id == evidence_id for item in self.record.evidence):
            raise EvidenceError(f"Evidence id already exists: {evidence_id}")
        record = EvidenceRecord(
            evidence_id=evidence_id,
            kind=kind,
            source_step_id=step_id,
            status="satisfied",
            selector="",
            value_sha256=_json_sha256(value),
            value=value,
        )
        self.record.evidence.append(record)
        index = next(
            (
                index
                for index, attempt in enumerate(self.record.attempts)
                if attempt.attempt_id == attempt_id
            ),
            None,
        )
        if index is None:
            raise EvidenceError(f"Tool Attempt does not exist: {attempt_id}")
        attempt = self.record.attempts[index]
        self.record.attempts[index] = replace(
            attempt, evidence_ids=(*attempt.evidence_ids, evidence_id)
        )
        self.harness._event(
            self.store,
            self.record,
            "evidence_recorded",
            {"evidence_id": evidence_id, "step_id": step_id, "kind": kind},
        )
        self.harness._persist(self.record, self.store)
        return record

    def complete_agent_run(
        self,
        agent_run_id: str,
        *,
        status: str,
        turns: int,
        final_artifact_id: str | None = None,
        error: Exception | None = None,
    ) -> None:
        index = self._agent_index(agent_run_id)
        current = self.record.agent_runs[index]
        self.record.agent_runs[index] = replace(
            current,
            status=status,
            finished_at=_now(),
            turns=turns,
            final_artifact_id=final_artifact_id,
            error=_serialize_error(error) if error is not None else None,
        )
        self.harness._event(
            self.store,
            self.record,
            "agent_run_completed",
            {
                "agent_run_id": agent_run_id,
                "status": status,
                "turns": turns,
                "final_artifact_id": final_artifact_id,
            },
        )
        self.harness._persist(self.record, self.store)

    def finalize(self) -> HarnessRunResult:
        self._ensure_mutable()
        if self.machine.state != "validating":
            self.harness._transition(self.machine, self.record, self.store, "validating")
        self.harness._finalize_validation(self.machine, self.record, self.store, self.output)
        return HarnessRunResult(self.record, {}, self.output)

    def fail(self, error: Exception) -> None:
        if self.record.task_status in TERMINAL_STATES:
            return
        self.record.errors.append(_serialize_error(error))
        self.harness._transition(self.machine, self.record, self.store, "failed")

    def wait_for_user(self, reason: str) -> None:
        """Persist a resumable boundary after an external model failure."""
        self._ensure_mutable()
        self.record.warnings.append(reason[:500])
        if self.machine.state == "executing":
            self.harness._transition(self.machine, self.record, self.store, "recovering")
        if self.machine.state == "recovering":
            self.harness._transition(self.machine, self.record, self.store, "waiting_for_user")

    def cancel(self) -> None:
        self._ensure_mutable()
        self.harness._cancel_running(self.record)
        self.harness._transition(self.machine, self.record, self.store, "cancelled")
        self.store.clear_cancel_request()

    def _attach_attempt(self, agent_run_id: str, attempt_id: str) -> None:
        index = self._agent_index(agent_run_id)
        current = self.record.agent_runs[index]
        self.record.agent_runs[index] = replace(
            current,
            tool_attempt_ids=(*current.tool_attempt_ids, attempt_id),
        )

    def _agent_index(self, agent_run_id: str) -> int:
        for index, item in enumerate(self.record.agent_runs):
            if item.agent_run_id == agent_run_id:
                return index
        raise HarnessError(f"Agent Run does not exist: {agent_run_id}")

    def _step(self, step_id: str) -> PlanStep:
        for step in self.plan.steps:
            if step.step_id == step_id:
                return step
        raise HarnessError(f"Plan Step does not exist: {step_id}")

    def _ensure_mutable(self) -> None:
        if self.record.task_status in TERMINAL_STATES:
            raise StateTransitionError(
                f"Terminal task state '{self.record.task_status}' cannot be modified"
            )

    def _binding(self, tool_name: str) -> ToolBindingRecord:
        current = ToolBindingRecord(**self.registry.binding_for(tool_name).to_dict())
        approved = next(
            (item for item in self.record.tool_bindings if item.tool_name == tool_name), None
        )
        if approved is None or approved != current:
            raise PlanPolicyError(f"Tool/Provider binding changed after plan approval: {tool_name}")
        return approved

    def _record_provider_events(self, events: tuple[Any, ...]) -> None:
        for item in events:
            self.harness._event(
                self.store,
                self.record,
                f"provider_{item.state}",
                {"provider": item.provider_id, **item.data},
            )


def create_task_spec(
    *,
    request: str,
    workflow: str,
    input_asset_ids: tuple[str, ...],
    allowed_tools: tuple[str, ...],
    task_id: str | None = None,
    required_validators: tuple[str, ...] | None = None,
    required_final_artifact_roles: tuple[str, ...] = (),
) -> TaskSpec:
    arguments: dict[str, Any] = {
        "task_id": task_id or str(uuid.uuid4()),
        "request": request,
        "workflow": workflow,
        "input_asset_ids": input_asset_ids,
        "allowed_tools": allowed_tools,
        "required_final_artifact_roles": required_final_artifact_roles,
    }
    if required_validators is not None:
        arguments["required_validators"] = required_validators
    return TaskSpec(
        **arguments,
    )


def evidence_value(record: RunRecord, evidence_id: str) -> Any:
    for item in record.evidence:
        if item.evidence_id == evidence_id and item.status == "satisfied":
            return item.value
    raise EvidenceError(f"Satisfied evidence does not exist: {evidence_id}")


def cancel_task_directory(
    output_dir: str | Path,
    *,
    reason: str = "user requested cancellation",
) -> tuple[str, RunRecord]:
    """Cancel an idle run immediately or leave a request for an active writer."""
    store = FileRunStore(output_dir)
    current = store.load()
    assert current is not None
    if current.task_status in TERMINAL_STATES:
        raise StateTransitionError(
            f"Terminal task state '{current.task_status}' cannot be cancelled"
        )
    store.request_cancel(reason)
    try:
        with store.lock():
            record = store.load()
            assert record is not None
            if record.task_status in TERMINAL_STATES:
                store.clear_cancel_request()
                return "already_terminal", record
            now = _now()
            for index, attempt in enumerate(record.attempts):
                if attempt.status == "running":
                    record.attempts[index] = replace(
                        attempt,
                        status="cancelled",
                        finished_at=now,
                        error={
                            "category": "Cancelled",
                            "message": reason[:500],
                            "retryable": False,
                        },
                    )
            for index, agent_run in enumerate(record.agent_runs):
                if agent_run.status == "running":
                    record.agent_runs[index] = replace(
                        agent_run,
                        status="cancelled",
                        finished_at=now,
                    )
            machine = TaskStateMachine(record.task_status)
            source, target = machine.transition("cancelled")
            record.task_status = target
            record.execution_status = "cancelled"
            record.updated_at = now
            store.save(record)
            store.append_event(
                {
                    "schema_version": "1.0",
                    "event_id": str(uuid.uuid4()),
                    "task_id": record.task_id,
                    "type": "task_state_changed",
                    "at": now,
                    "data": {"from": source, "to": target, "reason": reason[:500]},
                }
            )
            store.clear_cancel_request()
            return "cancelled", record
    except RunLockError:
        return "requested", current


def _topological_steps(steps: tuple[PlanStep, ...]) -> list[PlanStep]:
    by_id = {step.step_id: step for step in steps}
    remaining = dict(by_id)
    ordered: list[PlanStep] = []
    completed: set[str] = set()
    while remaining:
        ready = [step for step in remaining.values() if set(step.depends_on).issubset(completed)]
        if not ready:
            raise PlanPolicyError("ExecutionPlan dependencies contain a cycle")
        for step in steps:
            if step in ready and step.step_id in remaining:
                ordered.append(step)
                completed.add(step.step_id)
                del remaining[step.step_id]
    return ordered


def _json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    current = document
    for raw in pointer.lstrip("/").split("/"):
        if re.search(r"~(?![01])", raw):
            raise ValueError(f"JSON Pointer contains an invalid escape: {pointer}")
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                raise KeyError(f"JSON Pointer does not exist: {pointer}")
            current = current[token]
        elif isinstance(current, (list, tuple)):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as exc:
                raise IndexError(f"JSON Pointer does not exist: {pointer}") from exc
        else:
            raise TypeError(f"JSON Pointer cannot traverse scalar value: {pointer}")
    return current


def _json_sha256(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _serialize_error(exc: Exception) -> dict[str, Any]:
    return {
        "category": type(exc).__name__,
        "message": str(exc)[:2000],
        "retryable": False,
    }


def _validate_identifier(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise PlanPolicyError(f"{field_name} must contain 1-128 safe identifier characters")


def _now() -> str:
    return datetime.now(UTC).isoformat()
