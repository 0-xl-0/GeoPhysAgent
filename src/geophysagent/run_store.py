from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import socket
import tempfile
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from geophysagent.assets import DataAsset
from geophysagent.contracts import (
    AgentCheckpoint,
    AgentRunRecord,
    ArtifactManifest,
    ArtifactRecord,
    CompactionRecord,
    ContextBlock,
    ContextBudget,
    ContextSnapshot,
    EvidenceRecord,
    ExecutionPlan,
    ExpectedEvidence,
    PlanStep,
    RunRecord,
    StepAttempt,
    TaskSpec,
    ToolBindingRecord,
    ValidationExecutionRecord,
)


class RunStoreError(RuntimeError):
    """The task run cannot be persisted or reconstructed safely."""


class RunLockError(RunStoreError):
    """The task directory already has an active writer."""


class ArtifactStoreError(RunStoreError):
    """An artifact is missing, outside the run, or has changed."""


class RunDirectoryLock:
    """Portable single-writer lock with same-host stale PID recovery."""

    def __init__(self, root: Path, *, timeout: float = 0.0):
        self.root = root
        self.path = root / ".writer.lock"
        self.timeout = max(timeout, 0.0)
        self.token = uuid.uuid4().hex
        self._held = False

    def __enter__(self) -> "RunDirectoryLock":
        self.root.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if self._reclaim_stale():
                    continue
                if time.monotonic() >= deadline:
                    owner = _read_json_if_possible(self.path)
                    detail = (
                        f" pid={owner.get('pid')} host={owner.get('host')}"
                        if isinstance(owner, dict)
                        else ""
                    )
                    raise RunLockError(f"Task directory already has an active writer:{detail}")
                time.sleep(0.05)
                continue
            payload = json.dumps(
                {
                    "schema_version": "1.0",
                    "pid": os.getpid(),
                    "host": socket.gethostname(),
                    "token": self.token,
                    "created_at_unix": time.time(),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            try:
                os.write(descriptor, payload)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._held = True
            return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if not self._held:
            return
        owner = _read_json_if_possible(self.path)
        if isinstance(owner, dict) and owner.get("token") == self.token:
            try:
                self.path.unlink(missing_ok=True)
            except OSError as remove_error:
                if exc is None:
                    raise RunLockError(
                        f"Cannot release task writer lock: {remove_error}"
                    ) from remove_error
        self._held = False

    def _reclaim_stale(self) -> bool:
        owner = _read_json_if_possible(self.path)
        if not isinstance(owner, dict) or owner.get("host") != socket.gethostname():
            return False
        pid = owner.get("pid")
        if not isinstance(pid, int) or pid <= 0 or _pid_exists(pid):
            return False
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False


class FileRunStore:
    """File-backed task store with versioned checkpoints and an atomic commit pointer."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def lock(self, *, timeout: float = 0.0) -> RunDirectoryLock:
        return RunDirectoryLock(self.root, timeout=timeout)

    def initialize(
        self,
        *,
        task_spec: TaskSpec,
        plan: ExecutionPlan,
        assets: dict[str, DataAsset],
        record: RunRecord,
    ) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if (self.root / "task.json").exists() or (self.root / "checkpoint.json").exists():
            raise RunStoreError(f"Output directory already contains a Harness task: {self.root}")
        for asset_id in task_spec.input_asset_ids:
            if asset_id not in assets:
                raise RunStoreError(f"Task references an unregistered asset: {asset_id}")
        _atomic_json(self.root / "task.json", task_spec.to_dict())
        _atomic_json(self.root / "plan.json", plan.to_dict())
        _atomic_json(
            self.root / "input-manifest.json",
            {
                "schema_version": "1.0",
                "task_id": task_spec.task_id,
                "assets": [
                    assets[name].to_dict(disclose_path=False) for name in task_spec.input_asset_ids
                ],
            },
        )
        # Local-only recovery data. This file is never exposed to a model or event log.
        _atomic_json(
            self.root / "input-locations.json",
            {
                "schema_version": "1.0",
                "task_id": task_spec.task_id,
                "assets": [
                    {
                        "asset_id": name,
                        "source_path": str(assets[name].source_path),
                        "sha256": assets[name].sha256,
                        "size_bytes": assets[name].size_bytes,
                    }
                    for name in task_spec.input_asset_ids
                ],
            },
        )
        self.save(record)

    def save(self, record: RunRecord) -> None:
        existing = self.load(required=False)
        if existing is not None and existing.task_status in {
            "completed",
            "completed_with_warnings",
            "incomplete",
            "failed",
            "cancelled",
        }:
            if _terminal_signature(existing) != _terminal_signature(record):
                raise RunStoreError(
                    f"Terminal task '{existing.task_status}' cannot accept new attempts, "
                    "evidence, validations, artifacts, or state changes"
                )

        base_revision = max(
            record.snapshot_revision,
            existing.snapshot_revision if existing is not None else 0,
        )
        revision = base_revision + 1
        while (self.root / ".checkpoints" / f"{revision:08d}").exists():
            revision += 1
        if (
            record.agent_checkpoint is not None
            and record.agent_checkpoint.run_revision == base_revision + 1
        ):
            record.agent_checkpoint = replace(
                record.agent_checkpoint,
                run_revision=revision,
                artifact_manifest_revision=revision,
            )
        record.snapshot_revision = revision
        state = {
            "schema_version": record.schema_version,
            "task_id": record.task_id,
            "task_status": record.task_status,
            "execution_status": record.execution_status,
            "updated_at": record.updated_at,
            "snapshot_revision": revision,
        }
        manifest = ArtifactManifest(
            record.task_id,
            tuple(record.artifacts),
            snapshot_revision=revision,
        ).to_dict()
        snapshot_dir = self.root / ".checkpoints" / f"{revision:08d}"
        snapshot_dir.mkdir(parents=True, exist_ok=False)
        snapshot_files = {
            "run_record": snapshot_dir / "run-record.json",
            "state": snapshot_dir / "state.json",
            "artifact_manifest": snapshot_dir / "artifact-manifest.json",
        }
        _atomic_json(snapshot_files["run_record"], record.to_dict())
        _atomic_json(snapshot_files["state"], state)
        _atomic_json(snapshot_files["artifact_manifest"], manifest)

        # Root files are mirrors. checkpoint.json is committed last and is authoritative.
        _atomic_json(self.root / "run-record.json", record.to_dict())
        _atomic_json(self.root / "state.json", state)
        _atomic_json(self.root / "artifact-manifest.json", manifest)
        checkpoint = {
            "schema_version": "1.0",
            "task_id": record.task_id,
            "revision": revision,
            "committed_at_unix": time.time(),
            "files": {
                name: {
                    "path": path.relative_to(self.root).as_posix(),
                    "sha256": _sha256(path),
                }
                for name, path in snapshot_files.items()
            },
            "immutable": {
                name: _sha256(self.root / name)
                for name in (
                    "task.json",
                    "plan.json",
                    "input-manifest.json",
                    "input-locations.json",
                )
            },
        }
        _atomic_json(self.root / "checkpoint.json", checkpoint)

    def load(self, *, required: bool = True) -> RunRecord | None:
        checkpoint_path = self.root / "checkpoint.json"
        if checkpoint_path.is_file():
            checkpoint = _load_json(checkpoint_path, "checkpoint.json")
            entry = checkpoint.get("files", {}).get("run_record")
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise RunStoreError("checkpoint.json does not identify a run record")
            path = _safe_child(self.root, entry["path"])
            if not path.is_file() or _sha256(path) != entry.get("sha256"):
                raise RunStoreError("Committed run-record checkpoint is missing or corrupt")
            return _run_record_from_dict(_load_json(path, entry["path"]))
        legacy = self.root / "run-record.json"
        if legacy.is_file():
            return _run_record_from_dict(_load_json(legacy, "run-record.json"))
        if required:
            raise RunStoreError(f"Directory does not contain a Harness run: {self.root}")
        return None

    def load_task_plan(self) -> tuple[TaskSpec, ExecutionPlan]:
        return (
            _task_from_dict(_load_json(self.root / "task.json", "task.json")),
            _plan_from_dict(_load_json(self.root / "plan.json", "plan.json")),
        )

    def load_assets(self) -> dict[str, DataAsset]:
        manifest = _load_json(self.root / "input-manifest.json", "input-manifest.json")
        locations = _load_json(self.root / "input-locations.json", "input-locations.json")
        location_by_id = {
            item.get("asset_id"): item
            for item in locations.get("assets", [])
            if isinstance(item, dict)
        }
        assets: dict[str, DataAsset] = {}
        for payload in manifest.get("assets", []):
            if not isinstance(payload, dict):
                raise RunStoreError("input-manifest.json contains an invalid asset")
            asset_id = payload.get("asset_id")
            location = location_by_id.get(asset_id)
            if not isinstance(asset_id, str) or not isinstance(location, dict):
                raise RunStoreError(f"Local location is missing for asset: {asset_id}")
            assets[asset_id] = DataAsset(
                asset_id=asset_id,
                source_path=Path(str(location.get("source_path", ""))).resolve(),
                media_type=str(payload.get("media_type", "application/octet-stream")),
                format=str(payload.get("format", "unknown")),
                size_bytes=int(payload.get("size_bytes", 0)),
                sha256=str(payload.get("sha256", "")),
                data_type=str(payload.get("data_type", "unknown")),
                adapter_id=str(payload.get("adapter_id", "unknown")),
                capabilities=tuple(str(item) for item in payload.get("capabilities", [])),
                metadata=dict(payload.get("metadata", {})),
                schema_version=str(payload.get("schema_version", "1.0")),
            )
        return assets

    def append_event(self, event: dict[str, Any]) -> None:
        path = self.root / "events.jsonl"
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise RunStoreError(f"Cannot append task event: {exc}") from exc

    def read_events(self) -> tuple[list[dict[str, Any]], list[str]]:
        path = self.root / "events.jsonl"
        if not path.is_file():
            return [], ["events.jsonl is missing"]
        events: list[dict[str, Any]] = []
        issues: list[str] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    issues.append(f"events.jsonl line {line_number} is incomplete or invalid")
                    continue
                if isinstance(event, dict):
                    events.append(event)
                else:
                    issues.append(f"events.jsonl line {line_number} is not an object")
        return events, issues

    def event_count(self) -> int:
        events, _ = self.read_events()
        return len(events)

    def request_cancel(self, reason: str = "user requested cancellation") -> None:
        _atomic_json(
            self.root / "cancel-request.json",
            {
                "schema_version": "1.0",
                "reason": reason[:500],
                "requested_at_unix": time.time(),
            },
        )

    def cancel_requested(self) -> bool:
        return (self.root / "cancel-request.json").is_file()

    def clear_cancel_request(self) -> None:
        (self.root / "cancel-request.json").unlink(missing_ok=True)


class ArtifactStore:
    def __init__(self, run_root: str | Path):
        self.root = Path(run_root).resolve()

    def register(
        self,
        path: str | Path,
        *,
        artifact_id: str,
        role: str,
        created_by_step: str,
        evidence_id: str | None = None,
        media_type: str | None = None,
    ) -> ArtifactRecord:
        requested = Path(path)
        candidate = (
            requested.resolve() if requested.is_absolute() else (self.root / requested).resolve()
        )
        try:
            relative = candidate.relative_to(self.root)
        except ValueError as exc:
            raise ArtifactStoreError(
                f"Artifact must be inside the task output directory: {candidate}"
            ) from exc
        if requested.is_symlink() or not candidate.is_file() or candidate.is_symlink():
            raise ArtifactStoreError(f"Artifact is not a regular file: {relative.as_posix()}")
        return ArtifactRecord(
            artifact_id=artifact_id,
            relative_path=relative.as_posix(),
            media_type=media_type
            or mimetypes.guess_type(candidate.name)[0]
            or "application/octet-stream",
            role=role,
            size_bytes=candidate.stat().st_size,
            sha256=_sha256(candidate),
            created_by_step=created_by_step,
            evidence_id=evidence_id,
        )

    def verify(self, artifact: ArtifactRecord) -> None:
        candidate = _safe_child(self.root, artifact.relative_path)
        if not candidate.is_file():
            raise ArtifactStoreError(f"Artifact is missing: {artifact.relative_path}")
        if candidate.is_symlink():
            raise ArtifactStoreError(
                f"Artifact cannot be a symbolic link: {artifact.relative_path}"
            )
        if candidate.stat().st_size != artifact.size_bytes or _sha256(candidate) != artifact.sha256:
            raise ArtifactStoreError(f"Artifact integrity check failed: {artifact.relative_path}")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError) as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise RunStoreError(f"Cannot atomically persist {path.name}: {exc}") from exc


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunStoreError(f"Cannot read {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RunStoreError(f"{label} must contain a JSON object")
    return payload


def _read_json_if_possible(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _safe_child(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ArtifactStoreError(f"Path escapes the task directory: {relative}") from exc
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pid_exists(pid: int) -> bool:
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _terminal_signature(record: RunRecord) -> str:
    payload = {
        "task_status": record.task_status,
        "execution_status": record.execution_status,
        "attempts": [item.to_dict() for item in record.attempts],
        "evidence": [item.to_dict() for item in record.evidence],
        "artifacts": [item.to_dict() for item in record.artifacts],
        "agent_runs": [item.to_dict() for item in record.agent_runs],
        "validations": [item.to_dict() for item in record.validations],
        "warnings": record.warnings,
        "errors": record.errors,
        "context_snapshots": [item.to_dict() for item in record.context_snapshots],
        "compactions": [item.to_dict() for item in record.compactions],
        "agent_checkpoint": (
            record.agent_checkpoint.to_dict() if record.agent_checkpoint is not None else None
        ),
        "tool_bindings": [item.to_dict() for item in record.tool_bindings],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _task_from_dict(payload: dict[str, Any]) -> TaskSpec:
    return TaskSpec(
        task_id=str(payload["task_id"]),
        request=str(payload["request"]),
        workflow=str(payload["workflow"]),
        input_asset_ids=tuple(payload.get("input_asset_ids", [])),
        allowed_tools=tuple(payload.get("allowed_tools", [])),
        required_validators=tuple(payload.get("required_validators", ())),
        required_final_artifact_roles=tuple(payload.get("required_final_artifact_roles", ())),
        schema_version=str(payload.get("schema_version", "1.0")),
    )


def _evidence_from_dict(payload: dict[str, Any]) -> ExpectedEvidence:
    return ExpectedEvidence(
        evidence_id=str(payload["evidence_id"]),
        kind=payload["kind"],
        source_step_id=str(payload["source_step_id"]),
        selector=str(payload.get("selector", "")),
        description=str(payload.get("description", "")),
        required=bool(payload.get("required", True)),
        artifact_role=str(payload.get("artifact_role", "output")),
    )


def _step_from_dict(payload: dict[str, Any]) -> PlanStep:
    return PlanStep(
        step_id=str(payload["step_id"]),
        tool_name=str(payload["tool_name"]),
        arguments=dict(payload.get("arguments", {})),
        depends_on=tuple(payload.get("depends_on", [])),
        expected_evidence_ids=tuple(payload.get("expected_evidence_ids", [])),
        optional=bool(payload.get("optional", False)),
        max_attempts=int(payload.get("max_attempts", 1)),
        step_kind=payload.get("step_kind", "tool"),
    )


def _plan_from_dict(payload: dict[str, Any]) -> ExecutionPlan:
    return ExecutionPlan(
        plan_id=str(payload["plan_id"]),
        task_id=str(payload["task_id"]),
        workflow=str(payload["workflow"]),
        workflow_version=str(payload["workflow_version"]),
        objectives=tuple(payload.get("objectives", [])),
        steps=tuple(_step_from_dict(item) for item in payload.get("steps", [])),
        expected_evidence=tuple(
            _evidence_from_dict(item) for item in payload.get("expected_evidence", [])
        ),
        schema_version=str(payload.get("schema_version", "1.0")),
    )


def _run_record_from_dict(payload: dict[str, Any]) -> RunRecord:
    task = _task_from_dict(dict(payload["task_spec"]))
    plan = _plan_from_dict(dict(payload["plan"]))
    return RunRecord(
        task_id=str(payload["task_id"]),
        task_status=payload["task_status"],
        execution_status=payload["execution_status"],
        task_spec=task,
        plan=plan,
        created_at=str(payload["created_at"]),
        updated_at=str(payload["updated_at"]),
        attempts=[
            StepAttempt(**_tuple_fields(item, "evidence_ids"))
            for item in payload.get("attempts", [])
        ],
        evidence=[EvidenceRecord(**item) for item in payload.get("evidence", [])],
        artifacts=[ArtifactRecord(**item) for item in payload.get("artifacts", [])],
        agent_runs=[
            AgentRunRecord(**_tuple_fields(item, "tool_attempt_ids", "response_ids"))
            for item in payload.get("agent_runs", [])
        ],
        validations=[ValidationExecutionRecord(**item) for item in payload.get("validations", [])],
        warnings=list(payload.get("warnings", [])),
        errors=list(payload.get("errors", [])),
        context_snapshots=[
            _context_snapshot_from_dict(item) for item in payload.get("context_snapshots", [])
        ],
        compactions=[
            CompactionRecord(**_tuple_fields(item, "evidence_ids", "tool_attempt_ids"))
            for item in payload.get("compactions", [])
        ],
        agent_checkpoint=(
            _agent_checkpoint_from_dict(payload["agent_checkpoint"])
            if isinstance(payload.get("agent_checkpoint"), dict)
            else None
        ),
        tool_bindings=[
            ToolBindingRecord(**_tuple_fields(item, "permissions", "network_origins"))
            for item in payload.get("tool_bindings", [])
        ],
        snapshot_revision=int(payload.get("snapshot_revision", 0)),
        schema_version=str(payload.get("schema_version", "1.0")),
    )


def _context_snapshot_from_dict(payload: dict[str, Any]) -> ContextSnapshot:
    budget = ContextBudget(**dict(payload.get("budget", {})))
    blocks = tuple(
        ContextBlock(**_tuple_fields(item, "references")) for item in payload.get("blocks", [])
    )
    return ContextSnapshot(
        snapshot_id=str(payload["snapshot_id"]),
        agent_run_id=str(payload["agent_run_id"]),
        turn=int(payload["turn"]),
        policy_version=str(payload["policy_version"]),
        blocks=blocks,
        active_tools=tuple(payload.get("active_tools", [])),
        provider_versions=tuple(payload.get("provider_versions", [])),
        message_count=int(payload.get("message_count", 0)),
        estimated_tokens=int(payload.get("estimated_tokens", 0)),
        budget=budget,
        evidence_ids=tuple(payload.get("evidence_ids", [])),
        artifact_ids=tuple(payload.get("artifact_ids", [])),
        compaction_id=(
            str(payload["compaction_id"]) if payload.get("compaction_id") is not None else None
        ),
        context_sha256=str(payload.get("context_sha256", "")),
        schema_version=str(payload.get("schema_version", "1.0")),
    )


def _agent_checkpoint_from_dict(payload: dict[str, Any]) -> AgentCheckpoint:
    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        raise RunStoreError("RunRecord Agent checkpoint messages must be an array")
    return AgentCheckpoint(
        task_id=str(payload["task_id"]),
        turn=int(payload.get("turn", 0)),
        messages=tuple(dict(item) for item in messages),
        approved_plan=(
            dict(payload["approved_plan"])
            if isinstance(payload.get("approved_plan"), dict)
            else None
        ),
        approved_plan_id=(
            str(payload["approved_plan_id"])
            if payload.get("approved_plan_id") is not None
            else None
        ),
        completed_steps=tuple(payload.get("completed_steps", [])),
        evidence_ids=tuple(payload.get("evidence_ids", [])),
        model_error=(
            dict(payload["model_error"]) if isinstance(payload.get("model_error"), dict) else None
        ),
        resume_policy=str(payload.get("resume_policy", "manual_only")),
        compaction_id=(
            str(payload["compaction_id"]) if payload.get("compaction_id") is not None else None
        ),
        context_snapshot_id=(
            str(payload["context_snapshot_id"])
            if payload.get("context_snapshot_id") is not None
            else None
        ),
        event_cursor=int(payload.get("event_cursor", 0)),
        run_revision=int(payload.get("run_revision", 0)),
        artifact_manifest_revision=int(payload.get("artifact_manifest_revision", 0)),
        schema_version=str(payload.get("schema_version", "2.0")),
    )


def _tuple_fields(payload: dict[str, Any], *names: str) -> dict[str, Any]:
    result = dict(payload)
    for name in names:
        result[name] = tuple(result.get(name, []))
    return result
