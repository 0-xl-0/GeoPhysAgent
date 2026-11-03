from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from geophysagent.run_inspection import validate_run_directory
from geophysagent.run_store import FileRunStore, RunStoreError


class ReferenceEvaluationError(RuntimeError):
    """A reference suite or recorded run cannot be evaluated safely."""


AssertionOperator = Literal["exact", "approx", "range", "contains", "subset"]


@dataclass(frozen=True)
class ReferenceAssertion:
    assertion_id: str
    source: str
    selector: str
    operator: AssertionOperator
    expected: Any
    tolerance: float | None = None


@dataclass(frozen=True)
class ReferenceCase:
    case_id: str
    run_directory: str
    expected_workflow: str
    input_kind: Literal["asset", "external"] = "asset"
    required_capabilities: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    required_evidence_ids: tuple[str, ...] = ()
    required_validator_ids: tuple[str, ...] = ()
    required_artifact_roles: tuple[str, ...] = ()
    required_report_sections: tuple[str, ...] = ()
    require_context_snapshots: bool = False
    accepted_statuses: tuple[str, ...] = ("completed", "completed_with_warnings")
    assertions: tuple[ReferenceAssertion, ...] = ()
    forbidden_report_phrases: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReferenceSuite:
    suite_id: str
    version: str
    cases: tuple[ReferenceCase, ...]
    description: str = ""
    schema_version: str = "1.0"


@dataclass(frozen=True)
class EvaluationCheck:
    layer: str
    code: str
    passed: bool
    message: str


@dataclass(frozen=True)
class ReferenceRunEvaluation:
    case_id: str
    run_directory: str
    passed: bool
    metrics: dict[str, float | int | bool]
    checks: tuple[EvaluationCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReferenceSuiteEvaluation:
    suite_id: str
    suite_version: str
    passed: bool
    case_count: int
    passed_case_count: int
    metrics: dict[str, float | int]
    cases: tuple[ReferenceRunEvaluation, ...]
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_reference_suite(path: str | Path) -> ReferenceSuite:
    source = Path(path).resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReferenceEvaluationError(f"Cannot read reference suite: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReferenceEvaluationError("Reference suite must contain one JSON object")
    if str(payload.get("schema_version", "")) != "1.0":
        raise ReferenceEvaluationError("Unsupported reference suite schema_version")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ReferenceEvaluationError("Reference suite requires at least one case")
    cases: list[ReferenceCase] = []
    seen: set[str] = set()
    for item in raw_cases:
        if not isinstance(item, dict):
            raise ReferenceEvaluationError("Every reference case must be an object")
        case_id = _required_text(item, "case_id")
        if case_id in seen:
            raise ReferenceEvaluationError(f"Duplicate reference case: {case_id}")
        seen.add(case_id)
        input_kind = str(item.get("input_kind", "asset"))
        if input_kind not in {"asset", "external"}:
            raise ReferenceEvaluationError(f"Invalid input_kind for {case_id}: {input_kind}")
        assertions = tuple(_assertion(value, case_id) for value in item.get("assertions", []))
        cases.append(
            ReferenceCase(
                case_id=case_id,
                run_directory=_required_text(item, "run_directory"),
                expected_workflow=_required_text(item, "expected_workflow"),
                input_kind=input_kind,  # type: ignore[arg-type]
                required_capabilities=_texts(item, "required_capabilities"),
                allowed_tools=_texts(item, "allowed_tools"),
                required_tools=_texts(item, "required_tools"),
                required_evidence_ids=_texts(item, "required_evidence_ids"),
                required_validator_ids=_texts(item, "required_validator_ids"),
                required_artifact_roles=_texts(item, "required_artifact_roles"),
                required_report_sections=_texts(item, "required_report_sections"),
                require_context_snapshots=_boolean(item, "require_context_snapshots"),
                accepted_statuses=_texts(
                    item,
                    "accepted_statuses",
                    default=("completed", "completed_with_warnings"),
                ),
                assertions=assertions,
                forbidden_report_phrases=_texts(item, "forbidden_report_phrases"),
                metadata=(dict(item["metadata"]) if isinstance(item.get("metadata"), dict) else {}),
            )
        )
    return ReferenceSuite(
        suite_id=_required_text(payload, "suite_id"),
        version=_required_text(payload, "version"),
        cases=tuple(cases),
        description=str(payload.get("description", "")),
        schema_version="1.0",
    )


def evaluate_reference_suite(
    suite: ReferenceSuite, runs_root: str | Path
) -> ReferenceSuiteEvaluation:
    root = Path(runs_root).resolve()
    evaluations = tuple(
        evaluate_reference_run(case, _safe_case_directory(root, case.run_directory))
        for case in suite.cases
    )
    passed = sum(item.passed for item in evaluations)
    count = len(evaluations)
    return ReferenceSuiteEvaluation(
        suite_id=suite.suite_id,
        suite_version=suite.version,
        passed=passed == len(evaluations),
        case_count=count,
        passed_case_count=passed,
        metrics={
            "reference_case_pass_rate": passed / count,
            "plan_valid_rate": sum(bool(item.metrics["plan_valid"]) for item in evaluations)
            / count,
            "mean_tool_success_rate": sum(
                float(item.metrics["tool_success_rate"]) for item in evaluations
            )
            / count,
            "mean_evidence_completeness": sum(
                float(item.metrics["evidence_completeness"]) for item in evaluations
            )
            / count,
            "mean_validator_nonfailure_rate": sum(
                float(item.metrics["validator_nonfailure_rate"]) for item in evaluations
            )
            / count,
            "mean_artifact_completeness": sum(
                float(item.metrics["artifact_completeness"]) for item in evaluations
            )
            / count,
            "task_success_rate": sum(bool(item.metrics["task_success"]) for item in evaluations)
            / count,
            "model_input_tokens": sum(
                int(item.metrics["model_input_tokens"]) for item in evaluations
            ),
            "model_output_tokens": sum(
                int(item.metrics["model_output_tokens"]) for item in evaluations
            ),
            "model_latency_ms": sum(int(item.metrics["model_latency_ms"]) for item in evaluations),
        },
        cases=evaluations,
    )


def evaluate_reference_run(
    case: ReferenceCase, run_directory: str | Path
) -> ReferenceRunEvaluation:
    root = Path(run_directory).resolve()
    inspection = validate_run_directory(root)
    checks = [
        EvaluationCheck(
            "harness",
            "run.integrity",
            inspection.valid,
            "Run Store, input and artifact hashes are valid"
            if inspection.valid
            else "; ".join(item.message for item in inspection.issues),
        )
    ]
    try:
        store = FileRunStore(root)
        record = store.load()
        task, plan = store.load_task_plan()
        assets = store.load_assets()
    except RunStoreError as exc:
        checks.append(EvaluationCheck("harness", "run.load", False, str(exc)))
        return ReferenceRunEvaluation(
            case.case_id,
            str(root),
            False,
            _empty_metrics(),
            tuple(checks),
        )

    checks.append(
        _check(
            "task",
            "workflow.exact",
            task.workflow == case.expected_workflow and plan.workflow == case.expected_workflow,
            f"expected {case.expected_workflow}; observed task={task.workflow}, plan={plan.workflow}",
        )
    )
    step_ids = [step.step_id for step in plan.steps]
    plan_valid = (
        plan.task_id == task.task_id
        and plan.workflow == task.workflow
        and len(step_ids) == len(set(step_ids))
        and bool(plan.steps)
    )
    checks.append(
        _check(
            "plan",
            "plan.valid",
            plan_valid,
            "plan identity and step identities are valid"
            if plan_valid
            else "plan/task identity mismatch, empty plan, or duplicate step identity",
        )
    )
    checks.append(
        _check(
            "task",
            "status.accepted",
            record.task_status in case.accepted_statuses,
            f"observed {record.task_status}; accepted {', '.join(case.accepted_statuses)}",
        )
    )
    input_ok = bool(assets) if case.input_kind == "asset" else not assets
    checks.append(
        _check(
            "task",
            "input.kind",
            input_ok,
            f"expected {case.input_kind}; observed {len(assets)} registered asset(s)",
        )
    )
    observed_capabilities = {
        capability for asset in assets.values() for capability in asset.capabilities
    }
    checks.append(
        _set_check(
            "routing",
            "capabilities.required",
            set(case.required_capabilities),
            observed_capabilities,
        )
    )
    attempted_tools = {attempt.tool_name for attempt in record.attempts}
    terminal_attempts = all(attempt.status != "running" for attempt in record.attempts)
    identified_providers = all(
        attempt.provider for attempt in record.attempts if attempt.status == "completed"
    )
    checks.append(
        _check(
            "provider_tool",
            "attempts.terminal_and_identified",
            terminal_attempts and identified_providers,
            "all Tool attempts are terminal and completed attempts identify a Provider"
            if terminal_attempts and identified_providers
            else "a Tool attempt is running or a completed attempt has no Provider identity",
        )
    )
    if case.allowed_tools:
        unexpected = attempted_tools - set(case.allowed_tools)
        checks.append(
            _check(
                "routing",
                "tools.allowlist",
                not unexpected,
                "no unexpected tools" if not unexpected else f"unexpected: {sorted(unexpected)}",
            )
        )
    checks.append(
        _set_check("routing", "tools.required", set(case.required_tools), attempted_tools)
    )
    evidence_by_id = {item.evidence_id: item for item in record.evidence}
    satisfied_evidence = {
        evidence_id
        for evidence_id, evidence in evidence_by_id.items()
        if evidence.status == "satisfied"
    }
    checks.append(
        _set_check(
            "evidence",
            "evidence.required",
            set(case.required_evidence_ids),
            satisfied_evidence,
        )
    )
    context_ok = not case.require_context_snapshots or bool(record.context_snapshots)
    checks.append(
        _check(
            "context",
            "context.recorded",
            context_ok,
            "required model context snapshots are recorded"
            if case.require_context_snapshots and context_ok
            else "required model context snapshots are missing"
            if case.require_context_snapshots
            else "case does not require model context snapshots",
        )
    )
    checks.append(
        _check(
            "recovery",
            "checkpoint.committed",
            record.snapshot_revision > 0,
            f"authoritative checkpoint revision is {record.snapshot_revision}",
        )
    )
    validation_by_id = {item.validator_id: item.status for item in record.validations}
    required_validators = set(case.required_validator_ids)
    missing_validators = required_validators - set(validation_by_id)
    failed_validators = {
        validator for validator in required_validators if validation_by_id.get(validator) == "fail"
    }
    checks.append(
        _check(
            "validation",
            "validators.required",
            not missing_validators and not failed_validators,
            (
                "all required validators are present and non-failing"
                if not missing_validators and not failed_validators
                else f"missing={sorted(missing_validators)}, failed={sorted(failed_validators)}"
            ),
        )
    )
    roles = {artifact.role for artifact in record.artifacts}
    checks.append(
        _set_check(
            "artifacts",
            "artifact_roles.required",
            set(case.required_artifact_roles),
            roles,
        )
    )
    for assertion in case.assertions:
        checks.append(_evaluate_assertion(assertion, record.to_dict(), evidence_by_id, root))
    reports = [
        root / artifact.relative_path
        for artifact in record.artifacts
        if artifact.role in {"final_report", "supporting_report", "final_answer"}
        and artifact.media_type.startswith("text/")
    ]
    text = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in reports)
    if case.required_report_sections:
        missing_sections = [
            section for section in case.required_report_sections if f"## {section}" not in text
        ]
        checks.append(
            _check(
                "report",
                "report.semantic_sections",
                not missing_sections,
                "all semantic report sections are present"
                if not missing_sections
                else f"missing: {missing_sections}",
            )
        )
    if case.forbidden_report_phrases:
        found = [phrase for phrase in case.forbidden_report_phrases if phrase in text]
        checks.append(
            _check(
                "report",
                "report.forbidden_phrases",
                not found,
                "no forbidden phrases" if not found else f"found: {found}",
            )
        )

    tool_attempt_count = len(record.attempts)
    completed_tool_count = sum(item.status == "completed" for item in record.attempts)
    required_evidence_count = len(case.required_evidence_ids)
    required_validator_count = len(case.required_validator_ids)
    required_artifact_count = len(case.required_artifact_roles)
    metrics: dict[str, float | int | bool] = {
        "run_integrity": inspection.valid,
        "plan_valid": plan_valid,
        "tool_attempt_count": tool_attempt_count,
        "tool_success_rate": _ratio(completed_tool_count, tool_attempt_count),
        "evidence_completeness": _ratio(
            len(set(case.required_evidence_ids) & satisfied_evidence), required_evidence_count
        ),
        "validator_nonfailure_rate": _ratio(
            sum(validation_by_id.get(item) in {"pass", "warning"} for item in required_validators),
            required_validator_count,
        ),
        "artifact_completeness": _ratio(
            len(set(case.required_artifact_roles) & roles), required_artifact_count
        ),
        "task_success": record.task_status in case.accepted_statuses,
        "model_run_count": len(record.agent_runs),
        "model_input_tokens": sum(item.input_tokens for item in record.agent_runs),
        "model_output_tokens": sum(item.output_tokens for item in record.agent_runs),
        "model_latency_ms": sum(item.model_latency_ms for item in record.agent_runs),
    }
    return ReferenceRunEvaluation(
        case.case_id,
        str(root),
        all(item.passed for item in checks),
        metrics,
        tuple(checks),
    )


def write_reference_evaluation(evaluation: ReferenceSuiteEvaluation, path: str | Path) -> Path:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(evaluation.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return destination


def _assertion(payload: Any, case_id: str) -> ReferenceAssertion:
    if not isinstance(payload, dict):
        raise ReferenceEvaluationError(f"Assertion in {case_id} must be an object")
    operator = str(payload.get("operator", ""))
    if operator not in {"exact", "approx", "range", "contains", "subset"}:
        raise ReferenceEvaluationError(f"Unsupported assertion operator in {case_id}: {operator}")
    if "expected" not in payload:
        raise ReferenceEvaluationError(f"Assertion in {case_id} is missing expected")
    tolerance = payload.get("tolerance")
    return ReferenceAssertion(
        assertion_id=_required_text(payload, "assertion_id"),
        source=_required_text(payload, "source"),
        selector=str(payload.get("selector", "")),
        operator=operator,  # type: ignore[arg-type]
        expected=payload["expected"],
        tolerance=float(tolerance) if tolerance is not None else None,
    )


def _evaluate_assertion(
    assertion: ReferenceAssertion,
    record: dict[str, Any],
    evidence_by_id: dict[str, Any],
    root: Path,
) -> EvaluationCheck:
    try:
        source = _assertion_source(assertion.source, record, evidence_by_id, root)
        actual = _select(source, assertion.selector)
        passed = _compare(actual, assertion)
        message = f"expected {assertion.operator} {assertion.expected!r}; observed {actual!r}"
    except (KeyError, ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
        passed = False
        message = f"cannot evaluate assertion: {exc}"
    return EvaluationCheck("domain", assertion.assertion_id, passed, message)


def _assertion_source(
    source: str, record: dict[str, Any], evidence_by_id: dict[str, Any], root: Path
) -> Any:
    if source == "record":
        return record
    if source.startswith("evidence:"):
        evidence_id = source.split(":", 1)[1]
        return evidence_by_id[evidence_id].value
    if source.startswith("artifact:"):
        identity = source.split(":", 1)[1]
        artifacts = record.get("artifacts", [])
        selected = next(
            (
                item
                for item in artifacts
                if item.get("artifact_id") == identity or item.get("role") == identity
            ),
            None,
        )
        if selected is None:
            raise KeyError(f"artifact not found: {identity}")
        path = (root / str(selected["relative_path"])).resolve()
        path.relative_to(root)
        return json.loads(path.read_text(encoding="utf-8"))
    raise KeyError(f"unsupported assertion source: {source}")


def _select(payload: Any, selector: str) -> Any:
    if selector in {"", "/"}:
        return payload
    current = payload
    for raw in selector.lstrip("/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        current = current[int(token)] if isinstance(current, list) else current[token]
    return current


def _compare(actual: Any, assertion: ReferenceAssertion) -> bool:
    expected = assertion.expected
    if assertion.operator == "exact":
        return actual == expected
    if assertion.operator == "approx":
        tolerance = assertion.tolerance if assertion.tolerance is not None else 1e-6
        return math.isclose(float(actual), float(expected), rel_tol=tolerance, abs_tol=tolerance)
    if assertion.operator == "range":
        if not isinstance(expected, list) or len(expected) != 2:
            raise ValueError("range expectation must be [minimum, maximum]")
        return float(expected[0]) <= float(actual) <= float(expected[1])
    if assertion.operator == "contains":
        return expected in actual
    if assertion.operator == "subset":
        return set(expected).issubset(set(actual))
    return False


def _safe_case_directory(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ReferenceEvaluationError(f"Run directory escapes runs root: {relative}") from exc
    return candidate


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ReferenceEvaluationError(f"Required text field is missing: {key}")
    return value.strip()


def _texts(payload: dict[str, Any], key: str, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ReferenceEvaluationError(f"{key} must be an array of strings")
    return tuple(value)


def _boolean(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key, False)
    if not isinstance(value, bool):
        raise ReferenceEvaluationError(f"{key} must be a boolean")
    return value


def _check(layer: str, code: str, passed: bool, message: str) -> EvaluationCheck:
    return EvaluationCheck(layer, code, passed, message)


def _set_check(layer: str, code: str, required: set[str], observed: set[str]) -> EvaluationCheck:
    missing = required - observed
    return _check(
        layer,
        code,
        not missing,
        "all required values are present" if not missing else f"missing: {sorted(missing)}",
    )


def _ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator


def _empty_metrics() -> dict[str, float | int | bool]:
    return {
        "run_integrity": False,
        "plan_valid": False,
        "tool_attempt_count": 0,
        "tool_success_rate": 0.0,
        "evidence_completeness": 0.0,
        "validator_nonfailure_rate": 0.0,
        "artifact_completeness": 0.0,
        "task_success": False,
        "model_run_count": 0,
        "model_input_tokens": 0,
        "model_output_tokens": 0,
        "model_latency_ms": 0,
    }
