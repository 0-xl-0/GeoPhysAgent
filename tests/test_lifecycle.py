from __future__ import annotations

import tempfile
import unittest
import uuid
from unittest import mock
from pathlib import Path

from geophysagent.assets import register_asset
from geophysagent.contracts import ExecutionPlan, ExpectedEvidence, PlanStep
from geophysagent.domain import GeoToolResult, ValidationCheck, ValidationReport
from geophysagent.harness import (
    GeoDomainHarness,
    StateTransitionError,
    cancel_task_directory,
    create_task_spec,
)
from geophysagent.providers import (
    BuiltInToolProvider,
    ToolDefinition,
    ToolExecution,
    ToolProviderRegistry,
)
from geophysagent.run_inspection import validate_run_directory
from geophysagent.run_store import (
    FileRunStore,
    RunLockError,
    RunStoreError,
    _atomic_json,
)
from geophysagent.validation_runtime import ValidationCoordinator


FIXTURE = Path(__file__).parent / "fixtures" / "basic.las"


class SimulatedProcessTermination(BaseException):
    pass


def _registry(
    calls: list[int],
    *,
    replay_policy: str = "safe",
    failures: int = 0,
    report_text: str = "value\n",
) -> ToolProviderRegistry:
    provider = BuiltInToolProvider()

    def handler(arguments, context):
        calls.append(len(calls) + 1)
        if len(calls) <= failures:
            raise OSError("injected transient failure")
        artifact = context.output_dir / "result.md"
        artifact.write_text(report_text, encoding="utf-8")
        return ToolExecution(
            GeoToolResult(
                tool_name="test.lifecycle",
                tool_version="1.0.0",
                status="success",
                data={"value": 42, "artifact": str(artifact)},
            )
        )

    provider.register(
        ToolDefinition(
            name="test.lifecycle",
            version="1.0.0",
            description="lifecycle test tool",
            input_schema={
                "type": "object",
                "properties": {"asset_id": {"type": "string"}},
                "required": ["asset_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "value": {"type": "integer"},
                    "artifact": {"type": "string"},
                },
                "required": ["value", "artifact"],
                "additionalProperties": False,
            },
            replay_policy=replay_policy,
            idempotent=replay_policy == "safe",
        ),
        handler,
    )
    registry = ToolProviderRegistry()
    registry.register_provider(provider)
    return registry


def _contracts(
    *,
    max_attempts: int = 1,
    required_validators: tuple[str, ...] | None = None,
    final_report: bool = False,
):
    task = create_task_spec(
        request="exercise task lifecycle",
        workflow="lifecycle-test",
        input_asset_ids=("primary",),
        allowed_tools=("test.lifecycle",),
        required_validators=required_validators,
        required_final_artifact_roles=("final_report",) if final_report else (),
    )
    evidence = (
        ExpectedEvidence("value", "fact", "compute", "/value"),
        ExpectedEvidence(
            "report",
            "artifact",
            "compute",
            "/artifact",
            artifact_role="final_report" if final_report else "output",
        ),
    )
    plan = ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        task_id=task.task_id,
        workflow=task.workflow,
        workflow_version="1.0.0",
        objectives=("test lifecycle",),
        steps=(
            PlanStep(
                "compute",
                "test.lifecycle",
                {"asset_id": "primary"},
                expected_evidence_ids=("value", "report"),
                max_attempts=max_attempts,
            ),
        ),
        expected_evidence=evidence,
    )
    return task, plan


class LifecycleTests(unittest.TestCase):
    def test_atomic_write_failure_preserves_previous_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"revision": 1}\n', encoding="utf-8")
            with mock.patch(
                "geophysagent.run_store.os.replace",
                side_effect=OSError("injected replace interruption"),
            ):
                with self.assertRaises(RunStoreError):
                    _atomic_json(path, {"revision": 2})
            self.assertEqual(path.read_text(encoding="utf-8"), '{"revision": 1}\n')
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_each_primary_state_checkpoint_remains_readable_after_termination(self) -> None:
        asset = register_asset(FIXTURE)
        phases = (
            "after_state_created",
            "after_state_inspecting_input",
            "after_state_planning",
            "after_state_ready",
            "after_state_executing",
            "after_state_validating",
            "after_state_completed",
        )
        for phase in phases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                task, plan = _contracts()

                def terminate(current_phase, record, expected=phase):
                    if current_phase == expected:
                        raise SimulatedProcessTermination()

                with self.assertRaises(SimulatedProcessTermination):
                    GeoDomainHarness(_registry([]), fault_hook=terminate).execute(
                        task=task,
                        plan=plan,
                        assets={"primary": asset},
                        output_dir=directory,
                    )
                record = FileRunStore(directory).load()
                self.assertEqual(
                    f"after_state_{record.task_status}",
                    phase,
                )

    def test_checkpoint_is_authoritative_and_terminal_record_is_immutable(self) -> None:
        calls: list[int] = []
        task, plan = _contracts()
        asset = register_asset(FIXTURE)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = GeoDomainHarness(_registry(calls)).execute(
                task=task,
                plan=plan,
                assets={"primary": asset},
                output_dir=root,
            )
            self.assertEqual(result.record.task_status, "completed")
            self.assertTrue(validate_run_directory(root).valid)
            store = FileRunStore(root)
            with store.lock():
                with self.assertRaises(RunLockError):
                    with store.lock():
                        pass
            terminal = store.load()
            terminal.warnings.append("illegal mutation")
            with self.assertRaises(RunStoreError):
                store.save(terminal)

            (root / "state.json").write_text("{broken mirror", encoding="utf-8")
            self.assertEqual(store.load().task_status, "completed")
            inspection = validate_run_directory(root)
            self.assertFalse(inspection.valid)
            self.assertIn("snapshot.mirror_mismatch", {item.code for item in inspection.issues})

    def test_recovery_policy_retries_only_safe_transient_failures(self) -> None:
        asset = register_asset(FIXTURE)
        task, plan = _contracts(max_attempts=2)
        calls: list[int] = []
        with tempfile.TemporaryDirectory() as directory:
            result = GeoDomainHarness(_registry(calls, failures=1)).execute(
                task=task,
                plan=plan,
                assets={"primary": asset},
                output_dir=directory,
            )
            self.assertEqual(result.record.task_status, "completed")
            self.assertEqual(
                [item.status for item in result.record.attempts], ["failed", "completed"]
            )
            self.assertEqual(len(calls), 2)
            events = (Path(directory) / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn("recovery_started", events)

        calls = []
        task, plan = _contracts(max_attempts=2)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(OSError):
                GeoDomainHarness(_registry(calls, failures=2)).execute(
                    task=task,
                    plan=plan,
                    assets={"primary": asset},
                    output_dir=root,
                )
            failed = FileRunStore(root).load()
            self.assertEqual(failed.task_status, "failed")
            self.assertEqual(len(failed.attempts), 2)
            self.assertTrue(validate_run_directory(root).valid)

    def test_resume_reuses_intact_completed_step(self) -> None:
        asset = register_asset(FIXTURE)
        task, plan = _contracts(max_attempts=2)
        calls: list[int] = []

        def terminate(phase, record):
            if phase == "before_validation":
                raise SimulatedProcessTermination()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(SimulatedProcessTermination):
                GeoDomainHarness(_registry(calls), fault_hook=terminate).execute(
                    task=task,
                    plan=plan,
                    assets={"primary": asset},
                    output_dir=root,
                )
            interrupted = FileRunStore(root).load()
            self.assertEqual(interrupted.task_status, "validating")
            resumed = GeoDomainHarness(_registry(calls)).resume(root)
            self.assertEqual(resumed.record.task_status, "completed")
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(resumed.record.attempts), 1)

    def test_resume_replays_interrupted_safe_attempt_but_waits_for_manual_tool(self) -> None:
        asset = register_asset(FIXTURE)

        def terminate(phase, record):
            if phase == "after_attempt_started":
                raise SimulatedProcessTermination()

        safe_calls: list[int] = []
        safe_task, safe_plan = _contracts(max_attempts=2)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(SimulatedProcessTermination):
                GeoDomainHarness(_registry(safe_calls), fault_hook=terminate).execute(
                    task=safe_task,
                    plan=safe_plan,
                    assets={"primary": asset},
                    output_dir=root,
                )
            resumed = GeoDomainHarness(_registry(safe_calls)).resume(root)
            self.assertEqual(resumed.record.task_status, "completed")
            self.assertEqual(len(safe_calls), 1)
            self.assertEqual(
                [item.status for item in resumed.record.attempts],
                ["failed", "completed"],
            )

        manual_calls: list[int] = []
        manual_task, manual_plan = _contracts(max_attempts=1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(SimulatedProcessTermination):
                GeoDomainHarness(
                    _registry(manual_calls, replay_policy="manual"),
                    fault_hook=terminate,
                ).execute(
                    task=manual_task,
                    plan=manual_plan,
                    assets={"primary": asset},
                    output_dir=root,
                )
            resumed = GeoDomainHarness(_registry(manual_calls, replay_policy="manual")).resume(root)
            self.assertEqual(resumed.record.task_status, "waiting_for_user")
            self.assertEqual(manual_calls, [])

    def test_cancel_marks_interrupted_attempt_and_blocks_resume(self) -> None:
        asset = register_asset(FIXTURE)
        task, plan = _contracts(max_attempts=2)

        def terminate(phase, record):
            if phase == "after_attempt_started":
                raise SimulatedProcessTermination()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(SimulatedProcessTermination):
                GeoDomainHarness(_registry([]), fault_hook=terminate).execute(
                    task=task,
                    plan=plan,
                    assets={"primary": asset},
                    output_dir=root,
                )
            outcome, record = cancel_task_directory(root)
            self.assertEqual(outcome, "cancelled")
            self.assertEqual(record.task_status, "cancelled")
            self.assertEqual(record.attempts[0].status, "cancelled")
            with self.assertRaises(StateTransitionError):
                GeoDomainHarness(_registry([])).resume(root)

    def test_validation_coordinator_orders_all_layers_and_gates_success(self) -> None:
        order: list[str] = []
        coordinator = ValidationCoordinator()

        def validator(kind: str, status: str):
            def run(context):
                order.append(kind)
                check = ValidationCheck(f"test.{kind}", status, f"{kind} checked")
                return ValidationReport(status, (check,))

            return run

        validator_ids = tuple(f"test.{kind}" for kind in ("data", "semantic", "process", "result"))
        for kind, validator_id in zip(("data", "semantic", "process", "result"), validator_ids):
            coordinator.register(
                validator_id,
                kind,
                validator(kind, "fail" if kind == "semantic" else "pass"),
            )
        task, plan = _contracts(required_validators=validator_ids)
        with tempfile.TemporaryDirectory() as directory:
            result = GeoDomainHarness(_registry([]), validation_coordinator=coordinator).execute(
                task=task,
                plan=plan,
                assets={"primary": register_asset(FIXTURE)},
                output_dir=directory,
            )
            self.assertEqual(order, ["data", "semantic", "process", "result"])
            self.assertEqual(result.record.task_status, "incomplete")
            self.assertEqual(
                [item.kind for item in result.record.validations],
                ["data", "semantic", "process", "result"],
            )

    def test_final_report_must_reference_required_factual_evidence(self) -> None:
        task, plan = _contracts(final_report=True)
        with tempfile.TemporaryDirectory() as directory:
            result = GeoDomainHarness(
                _registry([], report_text="A report without its evidence id\n")
            ).execute(
                task=task,
                plan=plan,
                assets={"primary": register_asset(FIXTURE)},
                output_dir=directory,
            )
            self.assertEqual(result.record.task_status, "incomplete")
            failed = {item.validator_id: item.status for item in result.record.validations}
            self.assertEqual(failed["core.result.final_artifact_evidence"], "fail")

    def test_artifact_tampering_is_reported_by_offline_validation(self) -> None:
        task, plan = _contracts()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = GeoDomainHarness(_registry([])).execute(
                task=task,
                plan=plan,
                assets={"primary": register_asset(FIXTURE)},
                output_dir=root,
            )
            artifact = root / result.record.artifacts[0].relative_path
            artifact.write_text("tampered\n", encoding="utf-8")
            inspection = validate_run_directory(root)
            self.assertFalse(inspection.valid)
            self.assertIn("artifact.integrity", {item.code for item in inspection.issues})


if __name__ == "__main__":
    unittest.main()
