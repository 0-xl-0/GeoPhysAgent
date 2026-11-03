from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from geophysagent.application import execute_las_qc_task
from geophysagent.assets import register_asset
from geophysagent.contracts import ExecutionPlan, ExpectedEvidence, PlanStep
from geophysagent.domain import GeoToolResult
from geophysagent.harness import (
    GeoDomainHarness,
    PlanPolicyError,
    StateTransitionError,
    TaskStateMachine,
    create_task_spec,
)
from geophysagent.providers import (
    BuiltInToolProvider,
    ToolDefinition,
    ToolExecution,
    ToolProviderRegistry,
)
from geophysagent.run_store import ArtifactStore, ArtifactStoreError


FIXTURE = Path(__file__).parent / "fixtures" / "basic.las"


def _registry(*, missing_fact: bool = False, fail: bool = False) -> ToolProviderRegistry:
    provider = BuiltInToolProvider()

    def handler(arguments, context):
        if fail:
            raise RuntimeError("injected failure")
        artifact = context.output_dir / "data" / "result.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text('{"value": 42}\n', encoding="utf-8")
        data = {"artifacts": [str(artifact)]}
        if not missing_fact:
            data["facts"] = {"answer": 42}
        return ToolExecution(
            GeoToolResult(
                tool_name="test.compute",
                tool_version="1.0.0",
                status="success",
                provider="builtin",
                data=data,
            )
        )

    provider.register(
        ToolDefinition(
            name="test.compute",
            version="1.0.0",
            description="test",
            input_schema={
                "type": "object",
                "properties": {"asset_id": {"type": "string"}},
                "required": ["asset_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "facts": {"type": "object"},
                    "artifacts": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["artifacts"],
                "additionalProperties": False,
            },
        ),
        handler,
    )
    registry = ToolProviderRegistry()
    registry.register_provider(provider)
    return registry


def _contracts():
    task = create_task_spec(
        request="compute",
        workflow="test-workflow",
        input_asset_ids=("primary",),
        allowed_tools=("test.compute",),
    )
    evidence = (
        ExpectedEvidence("answer", "fact", "compute", "/facts/answer"),
        ExpectedEvidence("outputs", "artifact", "compute", "/artifacts"),
    )
    plan = ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        task_id=task.task_id,
        workflow=task.workflow,
        workflow_version="1.0.0",
        objectives=("compute",),
        steps=(
            PlanStep(
                "compute",
                "test.compute",
                {"asset_id": "primary"},
                expected_evidence_ids=("answer", "outputs"),
            ),
        ),
        expected_evidence=evidence,
    )
    return task, plan


class HarnessTests(unittest.TestCase):
    def test_plan_executes_with_state_attempt_evidence_and_artifact_records(self) -> None:
        task, plan = _contracts()
        asset = register_asset(FIXTURE)
        with tempfile.TemporaryDirectory() as directory:
            result = GeoDomainHarness(_registry()).execute(
                task=task,
                plan=plan,
                assets={"primary": asset},
                output_dir=directory,
            )
            root = Path(directory)
            persisted = json.loads((root / "run-record.json").read_text(encoding="utf-8"))
            self.assertEqual(result.record.task_status, "completed")
            self.assertEqual(result.record.execution_status, "completed")
            self.assertEqual(result.record.attempts[0].status, "completed")
            self.assertEqual(result.record.evidence[0].value, 42)
            self.assertEqual(len(result.record.artifacts), 1)
            self.assertEqual(persisted["task_status"], "completed")
            ArtifactStore(root).verify(result.record.artifacts[0])
            (root / result.record.artifacts[0].relative_path).write_text(
                '{"value": 43}\n', encoding="utf-8"
            )
            with self.assertRaises(ArtifactStoreError):
                ArtifactStore(root).verify(result.record.artifacts[0])
            events = (root / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn("task_state_changed", events)
            self.assertIn("artifact_registered", events)

    def test_missing_required_evidence_is_incomplete_not_success(self) -> None:
        task, plan = _contracts()
        asset = register_asset(FIXTURE)
        with tempfile.TemporaryDirectory() as directory:
            result = GeoDomainHarness(_registry(missing_fact=True)).execute(
                task=task,
                plan=plan,
                assets={"primary": asset},
                output_dir=directory,
            )
            self.assertEqual(result.record.task_status, "incomplete")
            self.assertEqual(result.record.execution_status, "completed")
            self.assertEqual(result.record.evidence[0].status, "missing")
            self.assertEqual(result.record.errors[0]["category"], "MissingEvidence")

    def test_execution_failure_persists_failed_authoritative_state(self) -> None:
        task, plan = _contracts()
        asset = register_asset(FIXTURE)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                GeoDomainHarness(_registry(fail=True)).execute(
                    task=task,
                    plan=plan,
                    assets={"primary": asset},
                    output_dir=directory,
                )
            state = json.loads((Path(directory) / "state.json").read_text(encoding="utf-8"))
            record = json.loads((Path(directory) / "run-record.json").read_text(encoding="utf-8"))
            self.assertEqual(state["task_status"], "failed")
            self.assertEqual(state["execution_status"], "failed")
            self.assertEqual(record["attempts"][0]["status"], "failed")

    def test_plan_policy_rejects_cycles_and_non_allowlisted_tools(self) -> None:
        task, plan = _contracts()
        asset = register_asset(FIXTURE)
        bad_step = PlanStep(
            "compute",
            "test.compute",
            {"asset_id": "primary"},
            depends_on=("compute",),
        )
        cyclic = ExecutionPlan(
            plan.plan_id,
            plan.task_id,
            plan.workflow,
            plan.workflow_version,
            plan.objectives,
            (bad_step,),
            (),
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PlanPolicyError):
                GeoDomainHarness(_registry()).execute(
                    task=task,
                    plan=cyclic,
                    assets={"primary": asset},
                    output_dir=directory,
                )
        forbidden = type(task)(
            task.task_id,
            task.request,
            task.workflow,
            task.input_asset_ids,
            (),
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PlanPolicyError):
                GeoDomainHarness(_registry()).execute(
                    task=forbidden,
                    plan=plan,
                    assets={"primary": asset},
                    output_dir=directory,
                )

    def test_terminal_state_and_artifact_path_escape_are_rejected(self) -> None:
        machine = TaskStateMachine()
        for state in (
            "inspecting_input",
            "planning",
            "ready",
            "executing",
            "validating",
            "completed",
        ):
            machine.transition(state)
        with self.assertRaises(StateTransitionError):
            machine.transition("executing")
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory).parent / "outside-artifact.txt"
            outside.write_text("not authorized", encoding="utf-8")
            try:
                with self.assertRaises(ArtifactStoreError):
                    ArtifactStore(directory).register(
                        outside,
                        artifact_id="outside",
                        role="output",
                        created_by_step="test",
                    )
            finally:
                outside.unlink(missing_ok=True)

    def test_real_las_reference_workflow_runs_through_harness(self) -> None:
        asset = register_asset(FIXTURE)
        with tempfile.TemporaryDirectory() as directory:
            analysis, result = execute_las_qc_task(
                asset=asset,
                request="检查 LAS 数据质量",
                output_dir=directory,
            )
            self.assertEqual(analysis.status, "completed_with_warnings")
            self.assertEqual(result.record.task_status, "completed_with_warnings")
            self.assertEqual(
                {item.evidence_id for item in result.record.evidence},
                {
                    "las-analysis",
                    "las-validation",
                    "las-result-artifact",
                    "las-final-report",
                    "las-figure",
                    "las-figure-metadata",
                },
            )
            self.assertEqual(len(result.record.artifacts), 4)


if __name__ == "__main__":
    unittest.main()
