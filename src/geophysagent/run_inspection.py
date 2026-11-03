from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from geophysagent.harness import TERMINAL_STATES
from geophysagent.run_store import ArtifactStore, ArtifactStoreError, FileRunStore, RunStoreError


@dataclass(frozen=True)
class RunIntegrityIssue:
    code: str
    severity: str
    message: str


@dataclass(frozen=True)
class RunInspection:
    valid: bool
    task_id: str | None
    task_status: str | None
    execution_status: str | None
    snapshot_revision: int | None
    timeline: tuple[dict[str, Any], ...]
    issues: tuple[RunIntegrityIssue, ...]
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_run_directory(run_root: str | Path) -> RunInspection:
    root = Path(run_root).resolve()
    store = FileRunStore(root)
    issues: list[RunIntegrityIssue] = []
    try:
        record = store.load()
    except RunStoreError as exc:
        return RunInspection(
            False,
            None,
            None,
            None,
            None,
            (),
            (RunIntegrityIssue("run.unreadable", "error", str(exc)),),
        )

    checkpoint_path = root / "checkpoint.json"
    if checkpoint_path.is_file():
        try:
            checkpoint = _json(checkpoint_path)
            if checkpoint.get("task_id") != record.task_id:
                _issue(
                    issues,
                    "checkpoint.task_id",
                    "error",
                    "Checkpoint task_id differs from RunRecord",
                )
            if int(checkpoint.get("revision", -1)) != record.snapshot_revision:
                _issue(
                    issues,
                    "checkpoint.revision",
                    "error",
                    "Checkpoint revision differs from RunRecord",
                )
            for name, entry in checkpoint.get("files", {}).items():
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    _issue(issues, "checkpoint.entry", "error", f"Invalid checkpoint entry: {name}")
                    continue
                path = _safe(root, entry["path"], issues)
                if path is None or not path.is_file():
                    _issue(
                        issues,
                        "checkpoint.file_missing",
                        "error",
                        f"Checkpoint file is missing: {entry.get('path')}",
                    )
                elif _sha256(path) != entry.get("sha256"):
                    _issue(
                        issues,
                        "checkpoint.hash",
                        "error",
                        f"Checkpoint file hash changed: {entry.get('path')}",
                    )
            for name, expected_hash in checkpoint.get("immutable", {}).items():
                path = root / name
                if not path.is_file() or _sha256(path) != expected_hash:
                    _issue(
                        issues,
                        "immutable.changed",
                        "error",
                        f"Immutable run file changed or is missing: {name}",
                    )
            mirrors = {
                "run_record": root / "run-record.json",
                "state": root / "state.json",
                "artifact_manifest": root / "artifact-manifest.json",
            }
            for name, mirror in mirrors.items():
                entry = checkpoint.get("files", {}).get(name)
                if isinstance(entry, dict):
                    committed = _safe(root, str(entry.get("path", "")), issues)
                    if committed is not None and (
                        not mirror.is_file() or _sha256(mirror) != _sha256(committed)
                    ):
                        _issue(
                            issues,
                            "snapshot.mirror_mismatch",
                            "error",
                            f"Root mirror is not the committed snapshot: {mirror.name}",
                        )
            checkpoint_root = root / ".checkpoints"
            if checkpoint_root.is_dir():
                for directory in checkpoint_root.iterdir():
                    if (
                        directory.is_dir()
                        and directory.name.isdigit()
                        and int(directory.name) > record.snapshot_revision
                    ):
                        _issue(
                            issues,
                            "snapshot.uncommitted",
                            "warning",
                            f"Uncommitted checkpoint directory detected: {directory.name}",
                        )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            _issue(issues, "checkpoint.invalid", "error", f"Cannot verify checkpoint: {exc}")

    temporary_files = [
        path.relative_to(root).as_posix() for path in root.rglob("*.tmp") if path.is_file()
    ]
    if temporary_files:
        _issue(
            issues,
            "snapshot.temporary_files",
            "warning",
            f"Temporary files left by an interrupted write: {', '.join(temporary_files[:10])}",
        )

    events, event_issues = store.read_events()
    for message in event_issues:
        _issue(issues, "events.invalid", "warning", message)
    previous_to: str | None = None
    for event in events:
        if event.get("task_id") != record.task_id:
            _issue(issues, "events.task_id", "error", "Event belongs to another task")
        if event.get("type") == "task_state_changed":
            data = event.get("data", {})
            source, target = data.get("from"), data.get("to")
            if previous_to is not None and source != previous_to:
                _issue(
                    issues,
                    "events.state_gap",
                    "warning",
                    f"State timeline gap: {previous_to} -> {source}",
                )
            previous_to = target

    agent_checkpoint = record.agent_checkpoint
    if agent_checkpoint is not None:
        if agent_checkpoint.task_id != record.task_id:
            _issue(
                issues,
                "agent_checkpoint.task_id",
                "error",
                "Agent checkpoint belongs to another task",
            )
        if not 0 < agent_checkpoint.run_revision <= record.snapshot_revision:
            _issue(
                issues,
                "agent_checkpoint.revision",
                "error",
                "Agent checkpoint is not linked to a committed RunRecord revision",
            )
        if agent_checkpoint.artifact_manifest_revision != agent_checkpoint.run_revision:
            _issue(
                issues,
                "agent_checkpoint.artifact_revision",
                "error",
                "Agent checkpoint and Artifact Manifest revisions differ",
            )
        if not 0 <= agent_checkpoint.event_cursor <= len(events):
            _issue(
                issues,
                "agent_checkpoint.event_cursor",
                "error",
                "Agent checkpoint event cursor is outside the event log",
            )
        snapshot_ids = {item.snapshot_id for item in record.context_snapshots}
        if (
            agent_checkpoint.context_snapshot_id is not None
            and agent_checkpoint.context_snapshot_id not in snapshot_ids
        ):
            _issue(
                issues,
                "agent_checkpoint.context_snapshot",
                "error",
                "Agent checkpoint references a missing Context Snapshot",
            )
        compaction_ids = {item.compaction_id for item in record.compactions}
        if (
            agent_checkpoint.compaction_id is not None
            and agent_checkpoint.compaction_id not in compaction_ids
        ):
            _issue(
                issues,
                "agent_checkpoint.compaction",
                "error",
                "Agent checkpoint references a missing Compaction Record",
            )
    context_ids: set[str] = set()
    agent_ids = {item.agent_run_id for item in record.agent_runs}
    compaction_ids = {item.compaction_id for item in record.compactions}
    for snapshot in record.context_snapshots:
        if snapshot.snapshot_id in context_ids:
            _issue(
                issues,
                "context_snapshot.duplicate",
                "error",
                f"Duplicate Context Snapshot id: {snapshot.snapshot_id}",
            )
        context_ids.add(snapshot.snapshot_id)
        if snapshot.agent_run_id not in agent_ids:
            _issue(
                issues,
                "context_snapshot.agent_run",
                "error",
                f"Context Snapshot references an unknown Agent Run: {snapshot.snapshot_id}",
            )
        if snapshot.estimated_tokens > snapshot.budget.max_total_tokens:
            _issue(
                issues,
                "context_snapshot.budget",
                "error",
                f"Context Snapshot exceeds its token budget: {snapshot.snapshot_id}",
            )
        if snapshot.compaction_id is not None and snapshot.compaction_id not in compaction_ids:
            _issue(
                issues,
                "context_snapshot.compaction",
                "error",
                f"Context Snapshot references a missing compaction: {snapshot.snapshot_id}",
            )

    artifact_store = ArtifactStore(root)
    for artifact in record.artifacts:
        try:
            artifact_store.verify(artifact)
        except ArtifactStoreError as exc:
            _issue(issues, "artifact.integrity", "error", str(exc))

    try:
        assets = store.load_assets()
        for asset in assets.values():
            if not asset.source_path.is_file():
                _issue(
                    issues,
                    "input.missing",
                    "error",
                    f"Input asset is missing: asset://{asset.asset_id}",
                )
            elif (
                asset.source_path.stat().st_size != asset.size_bytes
                or _sha256(asset.source_path) != asset.sha256
            ):
                _issue(
                    issues,
                    "input.changed",
                    "error",
                    f"Input asset changed: asset://{asset.asset_id}",
                )
    except RunStoreError as exc:
        _issue(issues, "input.manifest", "error", str(exc))

    if record.task_status in TERMINAL_STATES:
        if any(item.status == "running" for item in record.attempts):
            _issue(
                issues,
                "terminal.running_attempt",
                "error",
                "Terminal task contains a running Tool Attempt",
            )
        if any(item.status == "running" for item in record.agent_runs):
            _issue(
                issues,
                "terminal.running_agent",
                "error",
                "Terminal task contains a running Agent Run",
            )
    plan_steps = {item.step_id: item for item in record.plan.steps}
    for step_id in plan_steps:
        if plan_steps[step_id].step_kind != "tool":
            continue
        completed_count = sum(
            1
            for attempt in record.attempts
            if attempt.step_id == step_id and attempt.status == "completed"
        )
        if completed_count > 1:
            _issue(
                issues,
                "step.duplicate_completed_execution",
                "error",
                f"Plan Step {step_id} has {completed_count} completed Tool Attempts",
            )
    if record.task_status in {"completed", "completed_with_warnings"}:
        required = set(record.task_spec.required_validators)
        observed = {item.validator_id: item.status for item in record.validations}
        missing = sorted(required - set(observed))
        failed = sorted(
            name for name, status in observed.items() if name in required and status == "fail"
        )
        if missing:
            _issue(
                issues,
                "validation.missing",
                "error",
                f"Required validator records are missing: {', '.join(missing)}",
            )
        if failed:
            _issue(
                issues,
                "validation.failed",
                "error",
                f"Completed task has failed validators: {', '.join(failed)}",
            )

    timeline = rebuild_timeline(root, record=record, events=events)
    valid = not any(item.severity == "error" for item in issues)
    return RunInspection(
        valid,
        record.task_id,
        record.task_status,
        record.execution_status,
        record.snapshot_revision,
        tuple(timeline),
        tuple(issues),
    )


def rebuild_timeline(
    run_root: str | Path,
    *,
    record=None,
    events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    store = FileRunStore(run_root)
    selected_record = record or store.load()
    selected_events = events if events is not None else store.read_events()[0]
    timeline = [
        {
            "at": event.get("at"),
            "type": event.get("type"),
            "source": "events.jsonl",
            "data": event.get("data", {}),
        }
        for event in selected_events
    ]
    event_attempts = {
        item.get("data", {}).get("attempt_id")
        for item in selected_events
        if isinstance(item.get("data"), dict)
    }
    for attempt in selected_record.attempts:
        if attempt.attempt_id not in event_attempts:
            timeline.append(
                {
                    "at": attempt.finished_at or attempt.started_at,
                    "type": "tool_attempt_snapshot",
                    "source": "run-record.json",
                    "data": attempt.to_dict(),
                }
            )
    event_artifacts = {
        item.get("data", {}).get("artifact_id")
        for item in selected_events
        if isinstance(item.get("data"), dict)
    }
    for artifact in selected_record.artifacts:
        if artifact.artifact_id not in event_artifacts:
            timeline.append(
                {
                    "at": selected_record.updated_at,
                    "type": "artifact_manifest_snapshot",
                    "source": "artifact-manifest.json",
                    "data": artifact.to_dict(),
                }
            )
    for snapshot in selected_record.context_snapshots:
        timeline.append(
            {
                "at": selected_record.updated_at,
                "type": "context_snapshot",
                "source": "run-record.json",
                "data": {
                    "snapshot_id": snapshot.snapshot_id,
                    "agent_run_id": snapshot.agent_run_id,
                    "turn": snapshot.turn,
                    "estimated_tokens": snapshot.estimated_tokens,
                    "compaction_id": snapshot.compaction_id,
                },
            }
        )
    timeline.append(
        {
            "at": selected_record.updated_at,
            "type": "authoritative_state",
            "source": "state.json+checkpoint.json",
            "data": {
                "task_status": selected_record.task_status,
                "execution_status": selected_record.execution_status,
                "snapshot_revision": selected_record.snapshot_revision,
            },
        }
    )
    timeline.sort(key=lambda item: str(item.get("at") or ""))
    return timeline


def _issue(issues: list[RunIntegrityIssue], code: str, severity: str, message: str) -> None:
    issues.append(RunIntegrityIssue(code, severity, message))


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain an object")
    return payload


def _safe(root: Path, relative: str, issues: list[RunIntegrityIssue]) -> Path | None:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        _issue(
            issues, "path.escape", "error", f"Run metadata path escapes task directory: {relative}"
        )
        return None
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
