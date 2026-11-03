from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from geophysagent.contracts import RunRecord, ValidationExecutionRecord, ValidatorKind
from geophysagent.domain import ValidationCheck, ValidationReport
from geophysagent.run_store import ArtifactStore, ArtifactStoreError


class ValidatorRegistryError(RuntimeError):
    """A validator is missing, duplicated, or returned an invalid report."""


@dataclass(frozen=True)
class ValidationContext:
    record: RunRecord
    run_root: Path


Validator = Callable[[ValidationContext], ValidationReport]


@dataclass(frozen=True)
class ValidatorDefinition:
    validator_id: str
    kind: ValidatorKind
    validate: Validator


class ValidatorRegistry:
    def __init__(self, kind: ValidatorKind):
        self.kind = kind
        self._definitions: dict[str, ValidatorDefinition] = {}

    def register(self, validator_id: str, validator: Validator) -> None:
        if validator_id in self._definitions:
            raise ValidatorRegistryError(f"Validator already registered: {validator_id}")
        self._definitions[validator_id] = ValidatorDefinition(validator_id, self.kind, validator)

    def definition(self, validator_id: str) -> ValidatorDefinition:
        try:
            return self._definitions[validator_id]
        except KeyError as exc:
            raise ValidatorRegistryError(f"Validator is not registered: {validator_id}") from exc

    def ids(self) -> tuple[str, ...]:
        return tuple(self._definitions)


@dataclass(frozen=True)
class CoordinatedValidation:
    status: str
    executions: tuple[ValidationExecutionRecord, ...]


class ValidationCoordinator:
    """Runs registered validators in data/semantic/process/result order."""

    ORDER: tuple[ValidatorKind, ...] = ("data", "semantic", "process", "result")

    def __init__(self) -> None:
        self.registries = {kind: ValidatorRegistry(kind) for kind in self.ORDER}

    @classmethod
    def with_core_validators(cls) -> "ValidationCoordinator":
        coordinator = cls()
        coordinator.register("core.data.validation_evidence", "data", _validate_domain_evidence)
        coordinator.register(
            "core.process.required_evidence", "process", _validate_required_evidence
        )
        coordinator.register(
            "core.result.artifact_integrity", "result", _validate_artifact_integrity
        )
        coordinator.register(
            "core.result.final_artifact_evidence",
            "result",
            _validate_final_artifact_evidence,
        )
        coordinator.register(
            "core.result.claim_evidence",
            "result",
            _validate_claim_evidence,
        )
        return coordinator

    def register(self, validator_id: str, kind: ValidatorKind, validator: Validator) -> None:
        self.registries[kind].register(validator_id, validator)

    def run(
        self,
        record: RunRecord,
        run_root: str | Path,
        validator_ids: tuple[str, ...] | None = None,
    ) -> CoordinatedValidation:
        selected = (
            validator_ids if validator_ids is not None else record.task_spec.required_validators
        )
        if len(selected) != len(set(selected)):
            raise ValidatorRegistryError("Required validator ids must be unique")
        definitions: list[ValidatorDefinition] = []
        for kind in self.ORDER:
            registry = self.registries[kind]
            for validator_id in selected:
                if validator_id in registry.ids():
                    definitions.append(registry.definition(validator_id))
        found = {item.validator_id for item in definitions}
        missing = set(selected) - found
        if missing:
            raise ValidatorRegistryError(
                f"Required validators are not registered: {', '.join(sorted(missing))}"
            )

        context = ValidationContext(record, Path(run_root).resolve())
        executions: list[ValidationExecutionRecord] = []
        for definition in definitions:
            started = _now()
            report = definition.validate(context)
            if report.status not in {"pass", "warning", "fail"}:
                raise ValidatorRegistryError(
                    f"Validator {definition.validator_id} returned invalid status: {report.status}"
                )
            executions.append(
                ValidationExecutionRecord(
                    validator_id=definition.validator_id,
                    kind=definition.kind,
                    status=report.status,
                    started_at=started,
                    finished_at=_now(),
                    report=report.to_dict(),
                )
            )
        statuses = {item.status for item in executions}
        overall = "fail" if "fail" in statuses else "warning" if "warning" in statuses else "pass"
        return CoordinatedValidation(overall, tuple(executions))


def _validate_domain_evidence(context: ValidationContext) -> ValidationReport:
    checks: list[ValidationCheck] = []
    for evidence in context.record.evidence:
        if evidence.kind != "validation" or evidence.status != "satisfied":
            continue
        value = evidence.value
        status = value.get("status") if isinstance(value, dict) else None
        mapped = "pass" if status == "pass" else "warning" if status == "warning" else "fail"
        checks.append(
            ValidationCheck(
                f"domain.{evidence.evidence_id}",
                mapped,
                f"Domain validation evidence reports {status!r}",
                {"evidence_id": evidence.evidence_id},
            )
        )
    if not checks:
        checks.append(
            ValidationCheck(
                "domain.validation_evidence.none",
                "pass",
                "No domain validation evidence is required by this workflow",
            )
        )
    return ValidationReport(_aggregate(checks), tuple(checks))


def _validate_required_evidence(context: ValidationContext) -> ValidationReport:
    observed = {item.evidence_id: item for item in context.record.evidence}
    missing = [
        item.evidence_id
        for item in context.record.plan.expected_evidence
        if item.required
        and (item.evidence_id not in observed or observed[item.evidence_id].status != "satisfied")
    ]
    incomplete_steps: list[str] = []
    for step in context.record.plan.steps:
        if step.optional:
            continue
        if step.step_kind == "tool" and not any(
            attempt.step_id == step.step_id and attempt.status == "completed"
            for attempt in context.record.attempts
        ):
            incomplete_steps.append(step.step_id)
        elif step.step_kind == "agent" and not any(
            run.step_id == step.step_id and run.status == "completed"
            for run in context.record.agent_runs
        ):
            incomplete_steps.append(step.step_id)
        elif step.step_kind == "artifact":
            assigned = [
                item
                for item in context.record.plan.expected_evidence
                if item.source_step_id == step.step_id and item.required
            ]
            if any(
                item.evidence_id not in observed or observed[item.evidence_id].status != "satisfied"
                for item in assigned
            ):
                incomplete_steps.append(step.step_id)
    failures = []
    if missing:
        failures.append(f"Required evidence is missing or invalid: {', '.join(missing)}")
    if incomplete_steps:
        failures.append(f"Required Plan Steps are not complete: {', '.join(incomplete_steps)}")
    check = ValidationCheck(
        "process.required_evidence",
        "fail" if failures else "pass",
        ("; ".join(failures) if failures else "All required evidence is satisfied"),
        {"missing": missing, "incomplete_steps": incomplete_steps},
    )
    return ValidationReport(check.status, (check,))


def _validate_artifact_integrity(context: ValidationContext) -> ValidationReport:
    failures: list[str] = []
    store = ArtifactStore(context.run_root)
    for artifact in context.record.artifacts:
        try:
            store.verify(artifact)
        except ArtifactStoreError as exc:
            failures.append(str(exc))
    required_roles = set(context.record.task_spec.required_final_artifact_roles)
    present_roles = {item.role for item in context.record.artifacts}
    missing_roles = sorted(required_roles - present_roles)
    failures.extend(f"Required final artifact role is missing: {role}" for role in missing_roles)
    check = ValidationCheck(
        "result.artifact_integrity",
        "fail" if failures else "pass",
        "; ".join(failures) if failures else "All registered artifacts are present and unchanged",
        {"artifact_count": len(context.record.artifacts), "failures": failures},
    )
    return ValidationReport(check.status, (check,))


def _validate_final_artifact_evidence(context: ValidationContext) -> ValidationReport:
    required_roles = set(context.record.task_spec.required_final_artifact_roles)
    if not required_roles:
        check = ValidationCheck(
            "result.final_artifact_evidence.not_required",
            "pass",
            "This workflow does not require a final narrative artifact",
        )
        return ValidationReport("pass", (check,))
    final_artifacts = [item for item in context.record.artifacts if item.role in required_roles]
    required_evidence = [
        item.evidence_id
        for item in context.record.plan.expected_evidence
        if item.required and item.kind != "artifact"
    ]
    corpus = ""
    unreadable: list[str] = []
    for artifact in final_artifacts:
        path = (context.run_root / artifact.relative_path).resolve()
        if artifact.media_type.startswith("text/") or path.suffix.lower() in {
            ".md",
            ".json",
            ".txt",
        }:
            try:
                corpus += "\n" + path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                unreadable.append(f"{artifact.relative_path}: {exc}")
        else:
            unreadable.append(f"{artifact.relative_path}: final artifact is not textual")
    missing_ids = [evidence_id for evidence_id in required_evidence if evidence_id not in corpus]
    failures = unreadable + [
        f"Final artifact does not reference Evidence ID: {item}" for item in missing_ids
    ]
    check = ValidationCheck(
        "result.final_artifact_evidence",
        "fail" if failures else "pass",
        "; ".join(failures)
        if failures
        else "Final artifact references every required factual Evidence ID",
        {
            "final_artifact_count": len(final_artifacts),
            "required_evidence_ids": required_evidence,
            "missing_evidence_ids": missing_ids,
        },
    )
    return ValidationReport(check.status, (check,))


def _validate_claim_evidence(context: ValidationContext) -> ValidationReport:
    claim_map = next(
        (
            item
            for item in context.record.evidence
            if item.evidence_id == "agent-claim-map" and item.status == "satisfied"
        ),
        None,
    )
    failures: list[str] = []
    if claim_map is None or not isinstance(claim_map.value, dict):
        failures.append("Agent claim map is missing or invalid")
        claims: list[object] = []
    else:
        raw_claims = claim_map.value.get("claims")
        claims = raw_claims if isinstance(raw_claims, list) else []
        if not claims:
            failures.append("Agent claim map contains no factual references")
    evidence = {item.evidence_id: item for item in context.record.evidence}
    seen: set[str] = set()
    for raw in claims:
        if not isinstance(raw, dict):
            failures.append("Claim entry is not an object")
            continue
        claim_id = raw.get("claim_id")
        evidence_id = raw.get("evidence_id")
        selector = raw.get("selector")
        if not isinstance(claim_id, str) or not claim_id or claim_id in seen:
            failures.append(f"Claim has an invalid or duplicate id: {claim_id!r}")
            continue
        seen.add(claim_id)
        source = evidence.get(evidence_id)
        if source is None or source.status != "satisfied" or evidence_id == "agent-claim-map":
            failures.append(f"Claim {claim_id} references unavailable Evidence: {evidence_id}")
            continue
        if not isinstance(selector, str) or not selector.startswith("/"):
            failures.append(f"Claim {claim_id} has no valid JSON Pointer selector")
            continue
        try:
            resolved = _json_pointer(source.value, selector)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            failures.append(f"Claim {claim_id} selector cannot be resolved: {exc}")
            continue
        if isinstance(resolved, (dict, list)):
            failures.append(f"Claim {claim_id} must resolve to a scalar Evidence value")
            continue
        expected_hash = _json_sha256(resolved)
        if raw.get("value_sha256") != expected_hash or raw.get("value") != resolved:
            failures.append(f"Claim {claim_id} does not match its Evidence value")
    check = ValidationCheck(
        "result.claim_evidence",
        "fail" if failures else "pass",
        "; ".join(failures)
        if failures
        else "Every factual claim resolves to an immutable scalar Evidence value",
        {"claim_count": len(claims), "failures": failures},
    )
    return ValidationReport(check.status, (check,))


def _aggregate(checks: list[ValidationCheck]) -> str:
    statuses = {item.status for item in checks}
    return "fail" if "fail" in statuses else "warning" if "warning" in statuses else "pass"


def _json_pointer(value: object, selector: str) -> object:
    current = value
    for raw in selector.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                raise KeyError(token)
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit():
                raise ValueError(f"List token is not an index: {token}")
            current = current[int(token)]
        else:
            raise TypeError(f"Cannot descend through {type(current).__name__}")
    return current


def _json_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()
