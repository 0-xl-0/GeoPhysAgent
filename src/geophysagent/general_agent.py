from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from geophysagent.assets import DataAsset, register_asset
from geophysagent.builtin_provider import create_builtin_registry
from geophysagent.context import ContextAssembler, ContextAssemblyError
from geophysagent.contracts import (
    AgentCheckpoint,
    CompactionRecord,
    ContextBudget,
    EvidenceClaim,
    ExecutionPlan,
    ExpectedEvidence,
    PlanStep,
)
from geophysagent.explore import ExplorePythonProvider
from geophysagent.harness import (
    CancellationRequested,
    HarnessRunResult,
    PlanPolicy,
    TaskRunSession,
    create_task_spec,
)
from geophysagent.model import ModelClient, ModelClientError, ModelResponse, bind_model_session
from geophysagent.mcp import load_mcp_provider
from geophysagent.providers import ToolDefinition, ToolExecution, ToolProviderRegistry
from geophysagent.schema import validate_json_schema


class GeneralAgentError(RuntimeError):
    """The general LLM-first runtime could not safely complete the user task."""


@dataclass(frozen=True)
class CandidateStep:
    step_id: str
    model_tool: str
    tool_name: str
    purpose: str
    arguments_hint: dict[str, Any]
    depends_on: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ApprovedCandidatePlan:
    plan: ExecutionPlan
    steps: tuple[CandidateStep, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "execution_plan": self.plan.to_dict(),
            "agent_steps": [item.to_dict() for item in self.steps],
        }


@dataclass(frozen=True)
class GeneralAgentResult:
    answer: str
    provider: str
    model: str
    protocol: str
    turns: int
    evidence_ids: tuple[str, ...]
    candidate_plan: ApprovedCandidatePlan
    harness: HarnessRunResult


@dataclass(frozen=True)
class FinalAnswerSubmission:
    interpretation: str
    limitations: tuple[str, ...]
    claims: tuple[EvidenceClaim, ...]


SUBMIT_PLAN_NAME = "submit_execution_plan"
SUBMIT_FINAL_NAME = "submit_final_answer"
MAX_PLAN_STEPS = 6
MAX_TOOL_CALLS = 8
LEGACY_CHECKPOINT_NAME = "agent-checkpoint.json"


def create_general_agent_registry(
    mcp_manifests: tuple[str | Path, ...] = (),
) -> ToolProviderRegistry:
    registry = create_builtin_registry()
    try:
        registry.register_provider(ExplorePythonProvider())
        for manifest in mcp_manifests:
            registry.register_provider(load_mcp_provider(manifest))
    except Exception:
        registry.dispose()
        raise
    return registry


def run_general_agent(
    *,
    client: ModelClient,
    user_request: str,
    source_path: str | Path | None,
    output_dir: str | Path,
    max_turns: int = 12,
    max_model_retries: int = 2,
    registry: ToolProviderRegistry | None = None,
    on_text_delta: Callable[[str], None] | None = None,
    context_budget: ContextBudget | None = None,
    force_context_compaction: bool = False,
) -> GeneralAgentResult:
    if not user_request.strip():
        raise GeneralAgentError("User request cannot be empty")
    selected_registry = registry or create_general_agent_registry()
    try:
        asset = register_asset(source_path, asset_id="primary") if source_path is not None else None
        assets = {"primary": asset} if asset is not None else {}
        available = _available_agent_tools(asset, selected_registry)
        task, plan = _runtime_contracts(
            user_request,
            tuple(item.name for item in available),
            input_asset_ids=tuple(assets),
        )
        session = TaskRunSession(
            registry=selected_registry,
            task=task,
            plan=plan,
            assets=assets,
            output_dir=output_dir,
        )
    except BaseException:
        selected_registry.dispose()
        raise
    messages = [_initial_user_message(user_request, asset)]
    return _run_loop(
        client=client,
        session=session,
        available=available,
        messages=messages,
        approved=None,
        completed=(),
        evidence_ids=(),
        starting_turn=0,
        max_turns=max_turns,
        max_model_retries=max_model_retries,
        on_text_delta=on_text_delta,
        context_assembler=ContextAssembler(context_budget),
        force_context_compaction=force_context_compaction,
    )


def resume_general_agent(
    *,
    client: ModelClient,
    output_dir: str | Path,
    max_turns: int = 12,
    max_model_retries: int = 2,
    registry: ToolProviderRegistry | None = None,
    on_text_delta: Callable[[str], None] | None = None,
    context_budget: ContextBudget | None = None,
    force_context_compaction: bool = False,
) -> GeneralAgentResult:
    selected_registry = registry or create_general_agent_registry()
    session = TaskRunSession.reopen(
        registry=selected_registry,
        output_dir=output_dir,
    )
    try:
        checkpoint = _load_checkpoint(session)
        if session.task.workflow != "general-llm-agent":
            raise GeneralAgentError("Run directory is not a general LLM Agent task")
        asset = session.assets.get("primary")
        available = _available_agent_tools(asset, selected_registry)
        approved_payload = checkpoint.approved_plan
        artifact_plan_id: str | None = None
        if not isinstance(approved_payload, dict):
            approved_payload, artifact_plan_id = _candidate_submission_from_artifact(session.output)
        approved = (
            _approve_candidate(
                approved_payload,
                task_id=session.task.task_id,
                assets=session.assets,
                available=available,
                registry=selected_registry,
                plan_id=(
                    checkpoint.approved_plan_id if checkpoint.approved_plan_id else artifact_plan_id
                ),
            )
            if isinstance(approved_payload, dict)
            else None
        )
        persisted_dynamic_evidence = [
            item.evidence_id
            for item in session.record.evidence
            if item.source_step_id == "agent-runtime"
            and item.evidence_id
            not in {"agent-plan", "agent-evidence-index", "agent-tool-artifacts"}
        ]
        checkpoint_evidence = [str(item) for item in checkpoint.evidence_ids]
        resumed_evidence = tuple(dict.fromkeys([*checkpoint_evidence, *persisted_dynamic_evidence]))
        persisted_completed = (
            {
                item.step_id
                for item in approved.steps
                if f"{item.step_id}-result" in resumed_evidence
            }
            if approved is not None
            else set()
        )
        return _run_loop(
            client=client,
            session=session,
            available=available,
            messages=[dict(item) for item in checkpoint.messages],
            approved=approved,
            completed=tuple(
                dict.fromkeys(
                    [
                        *(str(item) for item in checkpoint.completed_steps),
                        *sorted(persisted_completed),
                    ]
                )
            ),
            evidence_ids=resumed_evidence,
            starting_turn=checkpoint.turn,
            max_turns=max_turns,
            max_model_retries=max_model_retries,
            on_text_delta=on_text_delta,
            context_assembler=ContextAssembler(context_budget),
            force_context_compaction=force_context_compaction,
        )
    except BaseException:
        session.close()
        raise


def write_general_agent_answer(result: GeneralAgentResult, output_dir: str | Path) -> Path:
    path = Path(output_dir).resolve() / "agent-response.md"
    if not path.is_file():
        raise GeneralAgentError("Final Agent answer is not registered in the TaskRun")
    return path


def create_waiting_general_agent_run(
    *,
    user_request: str,
    source_path: str | Path | None,
    output_dir: str | Path,
    provider: str,
    model: str,
    protocol: str,
    reason: str,
    registry: ToolProviderRegistry | None = None,
) -> str:
    """Create an auditable resumable TaskRun when model configuration is unavailable."""
    selected_registry = registry or create_general_agent_registry()
    try:
        asset = register_asset(source_path, asset_id="primary") if source_path is not None else None
        assets = {"primary": asset} if asset is not None else {}
        available = _available_agent_tools(asset, selected_registry)
        task, plan = _runtime_contracts(
            user_request,
            tuple(item.name for item in available),
            input_asset_ids=tuple(assets),
        )
        session = TaskRunSession(
            registry=selected_registry,
            task=task,
            plan=plan,
            assets=assets,
            output_dir=output_dir,
        )
    except BaseException:
        selected_registry.dispose()
        raise
    try:
        agent_run_id = session.start_agent_run(
            step_id="agent-runtime",
            runtime="general-llm-agent-v0.1",
            provider=provider,
            model=model,
            protocol=protocol,
        )
        error = GeneralAgentError(reason)
        session.complete_agent_run(agent_run_id, status="incomplete", turns=0, error=error)
        _save_checkpoint(
            session,
            [_initial_user_message(user_request, asset)],
            None,
            set(),
            [],
            0,
            model_error={"category": "configuration", "message": reason},
        )
        session.wait_for_user("Model configuration is unavailable; fix it and use 'run --resume'")
        return task.task_id
    finally:
        session.close()


def _run_loop(
    *,
    client: ModelClient,
    session: TaskRunSession,
    available: tuple[ToolDefinition, ...],
    messages: list[dict[str, Any]],
    approved: ApprovedCandidatePlan | None,
    completed: tuple[str, ...],
    evidence_ids: tuple[str, ...],
    starting_turn: int,
    max_turns: int,
    max_model_retries: int,
    on_text_delta: Callable[[str], None] | None,
    context_assembler: ContextAssembler,
    force_context_compaction: bool,
) -> GeneralAgentResult:
    agent_run_id = ""
    bind_model_session(client, session.task.task_id)
    completed_steps = set(completed)
    collected_evidence = list(evidence_ids)
    tool_call_count = sum(1 for item in session.record.attempts if item.step_id == "agent-runtime")
    last_turn = starting_turn
    force_compaction_pending = force_context_compaction
    try:
        agent_run_id = session.start_agent_run(
            step_id="agent-runtime",
            runtime="general-llm-agent-v0.1",
            provider=client.provider,
            model=client.model,
            protocol=str(getattr(client, "protocol", "unknown")),
        )
        for turn in range(starting_turn + 1, starting_turn + max_turns + 1):
            last_turn = turn
            if session.store.cancel_requested():
                raise CancellationRequested("Task cancellation was requested")
            remaining_before_call = (
                []
                if approved is None
                else [
                    item.step_id for item in approved.steps if item.step_id not in completed_steps
                ]
            )
            if approved is None:
                tools = [_submit_plan_tool(available)]
            elif remaining_before_call:
                eligible_names = {
                    item.model_tool
                    for item in approved.steps
                    if item.step_id not in completed_steps
                    and set(item.depends_on).issubset(completed_steps)
                }
                eligible = tuple(item for item in available if item.model_name in eligible_names)
                tools = _model_tool_definitions(eligible, session.registry)
            elif collected_evidence:
                tools = [_submit_final_answer_tool(tuple(collected_evidence))]
            else:
                tools = []
            _save_checkpoint(
                session,
                messages,
                approved,
                completed_steps,
                collected_evidence,
                turn - 1,
            )
            assembled = context_assembler.assemble(
                record=session.record,
                events=session.store.read_events()[0],
                asset=session.assets.get("primary"),
                tools=tools,
                active_tools=tuple(str(item["name"]) for item in tools),
                provider_versions=_provider_versions(tools, available, session.registry),
                agent_run_id=agent_run_id,
                turn=turn,
                phase_instruction=_phase_instruction(approved, remaining_before_call),
                force_compaction=force_compaction_pending,
            )
            force_compaction_pending = force_compaction_pending and assembled.compaction is None
            if assembled.compaction is not None:
                messages = [dict(item) for item in assembled.messages]
                _save_checkpoint(
                    session,
                    messages,
                    approved,
                    completed_steps,
                    collected_evidence,
                    turn - 1,
                    compaction=assembled.compaction,
                )
                assembled = context_assembler.assemble(
                    record=session.record,
                    events=session.store.read_events()[0],
                    asset=session.assets.get("primary"),
                    tools=tools,
                    active_tools=tuple(str(item["name"]) for item in tools),
                    provider_versions=_provider_versions(tools, available, session.registry),
                    agent_run_id=agent_run_id,
                    turn=turn,
                    phase_instruction=_phase_instruction(approved, remaining_before_call),
                )
            session.record_context_snapshot(assembled.snapshot)
            response = _complete_with_retry(
                client=client,
                session=session,
                agent_run_id=agent_run_id,
                system=assembled.system,
                messages=[dict(item) for item in assembled.messages],
                tools=[dict(item) for item in assembled.tools],
                max_model_retries=max_model_retries,
                on_text_delta=on_text_delta,
            )
            usage = response.usage or {}
            session.record_agent_turn(
                agent_run_id,
                turn,
                response_id=response.response_id,
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
                latency_ms=response.latency_ms,
            )
            messages.append({"role": "assistant", "content": list(response.content)})
            tool_uses = [block for block in response.content if block.get("type") == "tool_use"]
            text = "\n".join(
                str(block.get("text", "")).strip()
                for block in response.content
                if block.get("type") == "text" and str(block.get("text", "")).strip()
            )
            if on_text_delta is not None and text:
                on_text_delta(text)

            if approved is None:
                result_blocks: list[dict[str, Any]] = []
                for call in tool_uses:
                    call_id = str(call.get("id", ""))
                    if not call_id:
                        raise GeneralAgentError("Model returned a Tool Call without an id")
                    if call.get("name") != SUBMIT_PLAN_NAME:
                        result_blocks.append(
                            _tool_error(call_id, "The first Tool Call must submit an ExecutionPlan")
                        )
                        continue
                    try:
                        approved = _approve_candidate(
                            call.get("input"),
                            task_id=session.task.task_id,
                            assets=session.assets,
                            available=available,
                            registry=session.registry,
                        )
                        plan_path = session.output / "candidate-plan.json"
                        _atomic_json(plan_path, approved.to_dict())
                        session.register_planned_artifact(
                            step_id="agent-runtime",
                            evidence_id="agent-plan",
                            path=plan_path,
                            artifact_id="agent-plan",
                        )
                        result_blocks.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": call_id,
                                "content": json.dumps(
                                    {
                                        "status": "approved",
                                        "plan_id": approved.plan.plan_id,
                                        "steps": [
                                            {
                                                "step_id": item.step_id,
                                                "tool": item.model_tool,
                                                "depends_on": list(item.depends_on),
                                            }
                                            for item in approved.steps
                                        ],
                                    },
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                            }
                        )
                    except Exception as exc:
                        result_blocks.append(_tool_error(call_id, f"Plan rejected: {exc}"))
                if not result_blocks:
                    result_blocks.append(
                        {
                            "type": "text",
                            "text": "No plan was submitted. Call submit_execution_plan first.",
                        }
                    )
                messages.append({"role": "user", "content": result_blocks})
                _save_checkpoint(
                    session,
                    messages,
                    approved,
                    completed_steps,
                    collected_evidence,
                    turn,
                )
                continue

            if tool_uses:
                tool_results: list[dict[str, Any]] = []
                final_submission: FinalAnswerSubmission | None = None
                for call in tool_uses:
                    call_id = str(call.get("id", ""))
                    model_tool = str(call.get("name", ""))
                    arguments = call.get("input")
                    if not call_id:
                        raise GeneralAgentError("Model returned a Tool Call without an id")
                    if model_tool == SUBMIT_FINAL_NAME:
                        if remaining_before_call:
                            tool_results.append(
                                _tool_error(
                                    call_id,
                                    "Final answer is premature; approved Plan steps remain",
                                )
                            )
                            continue
                        try:
                            final_submission = _approve_final_submission(
                                arguments,
                                session=session,
                                available_evidence=tuple(collected_evidence),
                            )
                            tool_results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": call_id,
                                    "content": json.dumps(
                                        {
                                            "status": "accepted",
                                            "claim_count": len(final_submission.claims),
                                        },
                                        ensure_ascii=False,
                                        separators=(",", ":"),
                                    ),
                                }
                            )
                        except Exception as exc:
                            tool_results.append(
                                _tool_error(call_id, f"Final answer rejected: {exc}")
                            )
                        continue
                    if tool_call_count >= MAX_TOOL_CALLS:
                        tool_results.append(_tool_error(call_id, "Tool-call budget exhausted"))
                        continue
                    candidate = _ready_candidate_step(approved, model_tool, completed_steps)
                    if candidate is None:
                        tool_results.append(
                            _tool_error(
                                call_id,
                                "Tool is not present in an eligible approved Plan step, "
                                "or its dependencies are incomplete",
                            )
                        )
                        continue
                    if not isinstance(arguments, dict):
                        tool_results.append(
                            _tool_error(call_id, "Tool arguments must be an object")
                        )
                        continue
                    try:
                        execution, attempt_id = session.invoke_agent_tool(
                            agent_run_id=agent_run_id,
                            step_id="agent-runtime",
                            tool_name=candidate.tool_name,
                            arguments=arguments,
                        )
                        tool_call_count += 1
                        safe_data = _safe_tool_data(
                            execution, session.output, session.assets.get("primary")
                        )
                        evidence_data, model_data = _bound_tool_result(
                            session=session,
                            candidate_step_id=candidate.step_id,
                            tool_name=candidate.tool_name,
                            data=safe_data,
                            max_bytes=context_assembler.budget.max_tool_result_bytes,
                        )
                        evidence_id = f"{candidate.step_id}-result"
                        session.record_agent_evidence(
                            step_id="agent-runtime",
                            evidence_id=evidence_id,
                            kind="tool_result",
                            value={
                                "tool": candidate.tool_name,
                                "status": execution.result.status,
                                "data": evidence_data,
                            },
                            attempt_id=attempt_id,
                        )
                        collected_evidence.append(evidence_id)
                        validation = _validation_value(safe_data)
                        if validation is not None:
                            validation_id = f"{candidate.step_id}-validation"
                            session.record_agent_evidence(
                                step_id="agent-runtime",
                                evidence_id=validation_id,
                                kind="validation",
                                value=validation,
                                attempt_id=attempt_id,
                            )
                            collected_evidence.append(validation_id)
                        _register_tool_artifacts(
                            session,
                            candidate.step_id,
                            execution,
                        )
                        completed_steps.add(candidate.step_id)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": call_id,
                                "content": json.dumps(
                                    {
                                        "evidence_id": evidence_id,
                                        "tool": candidate.tool_name,
                                        "status": execution.result.status,
                                        "data": model_data,
                                    },
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                    allow_nan=False,
                                ),
                            }
                        )
                    except Exception as exc:
                        tool_results.append(
                            _tool_error(call_id, f"Harness rejected or failed the Tool: {exc}")
                        )
                messages.append({"role": "user", "content": tool_results})
                _save_checkpoint(
                    session,
                    messages,
                    approved,
                    completed_steps,
                    collected_evidence,
                    turn,
                )
                if final_submission is not None:
                    return _finalize_submission(
                        session=session,
                        client=client,
                        response=response,
                        agent_run_id=agent_run_id,
                        approved=approved,
                        completed_steps=completed_steps,
                        collected_evidence=collected_evidence,
                        tool_call_count=tool_call_count,
                        turn=turn,
                        submission=final_submission,
                    )
                continue

            remaining = [
                item.step_id for item in approved.steps if item.step_id not in completed_steps
            ]
            feedback = (
                "The final answer is premature. Complete approved Plan steps: "
                + ", ".join(remaining)
                if remaining
                else (
                    "Call submit_final_answer with scalar Evidence JSON Pointers. "
                    "Free text alone cannot complete the task."
                    if collected_evidence
                    else "A final answer requires at least one Tool Result Evidence ID."
                )
            )
            messages.append({"role": "user", "content": feedback})
            _save_checkpoint(
                session,
                messages,
                approved,
                completed_steps,
                collected_evidence,
                turn,
            )

        error = GeneralAgentError(
            f"Agent did not reach an evidence-bound stop condition within {max_turns} turns"
        )
        session.complete_agent_run(agent_run_id, status="incomplete", turns=last_turn, error=error)
        session.finalize()
        raise error
    except ModelClientError as exc:
        if agent_run_id and session.record.task_status not in {
            "completed",
            "completed_with_warnings",
            "incomplete",
            "failed",
            "cancelled",
        }:
            session.complete_agent_run(
                agent_run_id, status="incomplete", turns=last_turn, error=exc
            )
            _save_checkpoint(
                session,
                messages,
                approved,
                completed_steps,
                collected_evidence,
                last_turn,
                model_error={"category": exc.category, "message": str(exc)},
            )
            session.wait_for_user(
                f"Model unavailable ({exc.category}); rerun with 'run --resume' after fixing it"
            )
        raise GeneralAgentError(
            f"Model unavailable ({exc.category}); task is waiting for manual resume: {exc}"
        ) from exc
    except ContextAssemblyError as exc:
        if agent_run_id and session.record.task_status not in {
            "completed",
            "completed_with_warnings",
            "incomplete",
            "failed",
            "cancelled",
        }:
            session.complete_agent_run(
                agent_run_id,
                status="incomplete",
                turns=last_turn,
                error=exc,
            )
            _save_checkpoint(
                session,
                messages,
                approved,
                completed_steps,
                collected_evidence,
                max(starting_turn, last_turn - 1),
                model_error={"category": "context_budget", "message": str(exc)},
            )
            session.wait_for_user(
                "Context assembly exceeded policy limits; adjust the approved budget and resume"
            )
        raise GeneralAgentError(
            f"Context assembly failed; task is waiting for manual resume: {exc}"
        ) from exc
    except CancellationRequested as exc:
        if agent_run_id:
            session.complete_agent_run(agent_run_id, status="cancelled", turns=last_turn, error=exc)
        session.cancel()
        raise GeneralAgentError("Agent task was cancelled") from exc
    except Exception as exc:
        if agent_run_id and session.record.task_status not in {
            "completed",
            "completed_with_warnings",
            "incomplete",
            "failed",
            "cancelled",
        }:
            session.complete_agent_run(agent_run_id, status="failed", turns=last_turn, error=exc)
            session.fail(exc)
        raise
    finally:
        session.close()


def _complete_with_retry(
    *,
    client: ModelClient,
    session: TaskRunSession,
    agent_run_id: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    max_model_retries: int,
    on_text_delta: Callable[[str], None] | None,
) -> ModelResponse:
    for attempt in range(max_model_retries + 1):
        try:
            stream = getattr(client, "stream_complete", None)
            if callable(stream):
                return stream(
                    system=system,
                    messages=messages,
                    tools=tools,
                    on_text_delta=on_text_delta,
                )
            return client.complete(system=system, messages=messages, tools=tools)
        except ModelClientError as exc:
            if not exc.retryable or attempt >= max_model_retries:
                raise
            session.record_agent_retry(
                agent_run_id,
                category=exc.category,
                retry_number=attempt + 1,
            )
            time.sleep(min(0.2 * (attempt + 1), 0.5))
    raise AssertionError("unreachable model retry state")


def _available_agent_tools(
    asset: DataAsset | None, registry: ToolProviderRegistry
) -> tuple[ToolDefinition, ...]:
    available = tuple(
        definition
        for definition in registry.model_definitions()
        if not definition.input_capabilities
        or (
            asset is not None
            and all(
                capability in asset.capabilities for capability in definition.input_capabilities
            )
        )
    )
    if not available:
        if asset is None:
            raise GeneralAgentError(
                "No enabled asset-independent Integration Tool is available for this task"
            )
        raise GeneralAgentError("No authorized Agent Tool is available for this asset")
    return available


def _model_tool_definitions(
    available: tuple[ToolDefinition, ...], registry: ToolProviderRegistry
) -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    for definition in available:
        schema = json.loads(json.dumps(definition.input_schema))
        asset_property = schema.get("properties", {}).get("asset_id")
        if isinstance(asset_property, dict):
            asset_property["enum"] = ["primary"]
        assert definition.model_name is not None
        definitions.append(
            {
                "name": definition.model_name,
                "description": definition.model_description(),
                "input_schema": schema,
            }
        )
    return definitions


def _submit_plan_tool(available: tuple[ToolDefinition, ...]) -> dict[str, Any]:
    return {
        "name": SUBMIT_PLAN_NAME,
        "description": "提交结构化候选 ExecutionPlan，只有审批通过后才会开放专业 Tool。",
        "input_schema": {
            "type": "object",
            "properties": {
                "objective": {"type": "string", "minLength": 1, "maxLength": 1000},
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_PLAN_STEPS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "step_id": {
                                "type": "string",
                                "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
                            },
                            "tool": {
                                "type": "string",
                                "enum": [item.model_name for item in available],
                            },
                            "purpose": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 500,
                            },
                            "arguments_hint": {"type": "object"},
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                                "uniqueItems": True,
                                "maxItems": MAX_PLAN_STEPS,
                            },
                        },
                        "required": ["step_id", "tool", "purpose"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["objective", "steps"],
            "additionalProperties": False,
        },
    }


def _submit_final_answer_tool(evidence_ids: tuple[str, ...]) -> dict[str, Any]:
    return {
        "name": SUBMIT_FINAL_NAME,
        "description": (
            "简洁提交最终综合，不重复 Tool 原始结果。事实引用必须选择 Harness 已返回的 "
            "Evidence ID，并用 JSON Pointer 指向其中的标量值；interpretation 是模型解释，"
            "不会被提升为 Evidence。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "interpretation": {"type": "string", "minLength": 1, "maxLength": 1200},
                "claims": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 12,
                    "items": {
                        "type": "object",
                        "properties": {
                            "evidence_id": {"type": "string", "enum": list(evidence_ids)},
                            "selector": {
                                "type": "string",
                                "minLength": 2,
                                "maxLength": 500,
                                "pattern": "^/",
                            },
                        },
                        "required": ["evidence_id", "selector"],
                        "additionalProperties": False,
                    },
                },
                "limitations": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {"type": "string", "minLength": 1, "maxLength": 300},
                },
            },
            "required": ["interpretation", "claims", "limitations"],
            "additionalProperties": False,
        },
    }


def _provider_versions(
    tools: list[dict[str, Any]],
    available: tuple[ToolDefinition, ...],
    registry: ToolProviderRegistry,
) -> tuple[str, ...]:
    by_model_name = {item.model_name: item for item in available}
    versions: list[str] = []
    for tool in tools:
        name = str(tool.get("name", ""))
        if name in {SUBMIT_PLAN_NAME, SUBMIT_FINAL_NAME}:
            versions.append(f"runtime:{name}@1.0")
            continue
        selected = by_model_name.get(name)
        if selected is None:
            continue
        binding = registry.binding_for(selected.name)
        parts = [
            f"{binding.provider_id}:{binding.tool_name}@{binding.tool_version}",
            f"provider={binding.provider_version}",
            f"schema={binding.tool_schema_version}",
            "permissions=" + ",".join(binding.permissions),
        ]
        if binding.server_id is not None:
            parts.append(f"server={binding.server_id}@{binding.server_version or 'unknown'}")
        if binding.protocol_version is not None:
            parts.append(f"protocol={binding.protocol_version}")
        if binding.network_origins:
            origin_payload = json.dumps(
                binding.network_origins, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            parts.append(f"network_origins_sha256={hashlib.sha256(origin_payload).hexdigest()}")
        if binding.cwd_fingerprint is not None:
            parts.append(f"cwd_sha256={binding.cwd_fingerprint}")
        versions.append("|".join(parts))
    return tuple(versions)


def _approve_final_submission(
    payload: Any,
    *,
    session: TaskRunSession,
    available_evidence: tuple[str, ...],
) -> FinalAnswerSubmission:
    if not isinstance(payload, dict):
        raise GeneralAgentError("Final answer submission must be an object")
    validate_json_schema(
        payload,
        _submit_final_answer_tool(available_evidence)["input_schema"],
        subject="Final answer submission",
    )
    evidence_by_id = {item.evidence_id: item for item in session.record.evidence}
    claims: list[EvidenceClaim] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(payload["claims"], start=1):
        evidence_id = str(raw["evidence_id"])
        selector = str(raw["selector"])
        key = (evidence_id, selector)
        if key in seen:
            raise GeneralAgentError(
                f"Final answer repeats Evidence selector: {evidence_id}{selector}"
            )
        seen.add(key)
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None or evidence.status != "satisfied":
            raise GeneralAgentError(f"Final answer references unavailable Evidence: {evidence_id}")
        if evidence.kind == "artifact":
            raise GeneralAgentError("Factual claims must resolve through non-artifact Evidence")
        try:
            value = _json_pointer(evidence.value, selector)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise GeneralAgentError(
                f"Evidence selector cannot be resolved: {evidence_id}{selector}: {exc}"
            ) from exc
        if isinstance(value, (dict, list)):
            raise GeneralAgentError(
                f"Evidence selector must resolve to a scalar value: {evidence_id}{selector}"
            )
        claims.append(
            EvidenceClaim(
                claim_id=f"claim-{index:02d}",
                evidence_id=evidence_id,
                selector=selector,
                value_sha256=_json_sha256(value),
                value=value,
            )
        )
    return FinalAnswerSubmission(
        interpretation=str(payload["interpretation"]).strip(),
        limitations=tuple(str(item).strip() for item in payload["limitations"]),
        claims=tuple(claims),
    )


def _approve_candidate(
    payload: Any,
    *,
    task_id: str,
    assets: dict[str, DataAsset],
    available: tuple[ToolDefinition, ...],
    registry: ToolProviderRegistry,
    plan_id: str | None = None,
) -> ApprovedCandidatePlan:
    if not isinstance(payload, dict):
        raise GeneralAgentError("Candidate plan must be an object")
    validate_json_schema(
        payload, _submit_plan_tool(available)["input_schema"], subject="Candidate ExecutionPlan"
    )
    lookup = {item.model_name: item for item in available}
    candidate_steps: list[CandidateStep] = []
    plan_steps: list[PlanStep] = []
    for raw in payload["steps"]:
        selected = lookup[str(raw["tool"])]
        dependencies = tuple(str(item) for item in raw.get("depends_on", []))
        hint = raw.get("arguments_hint", {})
        assert isinstance(hint, dict)
        candidate = CandidateStep(
            step_id=str(raw["step_id"]),
            model_tool=selected.model_name,
            tool_name=selected.name,
            purpose=str(raw["purpose"]),
            arguments_hint=dict(hint),
            depends_on=dependencies,
        )
        candidate_steps.append(candidate)
        plan_steps.append(
            PlanStep(
                step_id=candidate.step_id,
                tool_name=candidate.tool_name,
                arguments=dict(candidate.arguments_hint),
                depends_on=candidate.depends_on,
            )
        )
    candidate_task = create_task_spec(
        request=str(payload["objective"]),
        workflow="general-agent-candidate",
        input_asset_ids=tuple(assets),
        allowed_tools=tuple(item.name for item in available),
        task_id=task_id,
        required_validators=(),
    )
    plan = ExecutionPlan(
        plan_id=plan_id or str(uuid.uuid4()),
        task_id=task_id,
        workflow=candidate_task.workflow,
        workflow_version="0.1.0",
        objectives=(str(payload["objective"]),),
        steps=tuple(plan_steps),
        expected_evidence=(),
    )
    PlanPolicy().validate(candidate_task, plan, registry, assets)
    return ApprovedCandidatePlan(plan, tuple(candidate_steps))


def _ready_candidate_step(
    approved: ApprovedCandidatePlan,
    model_tool: str,
    completed: set[str],
) -> CandidateStep | None:
    for item in approved.steps:
        if (
            item.model_tool == model_tool
            and item.step_id not in completed
            and set(item.depends_on).issubset(completed)
        ):
            return item
    return None


def _runtime_contracts(
    request: str,
    allowed_tools: tuple[str, ...],
    *,
    input_asset_ids: tuple[str, ...] = ("primary",),
) -> tuple[Any, ExecutionPlan]:
    task = create_task_spec(
        request=request,
        workflow="general-llm-agent",
        input_asset_ids=input_asset_ids,
        allowed_tools=allowed_tools,
        required_validators=(
            "core.data.validation_evidence",
            "core.process.required_evidence",
            "core.result.artifact_integrity",
            "core.result.final_artifact_evidence",
            "core.result.claim_evidence",
        ),
        required_final_artifact_roles=("final_answer",),
    )
    evidence = (
        ExpectedEvidence("agent-plan", "artifact", "agent-runtime", artifact_role="execution_plan"),
        ExpectedEvidence(
            "agent-evidence-index",
            "fact",
            "agent-runtime",
            description="Evidence IDs produced by approved Tool Attempts",
        ),
        ExpectedEvidence(
            "agent-tool-artifacts",
            "artifact",
            "agent-runtime",
            required=False,
            artifact_role="supporting_result",
        ),
        ExpectedEvidence("agent-trace", "artifact", "final-answer", artifact_role="trace"),
        ExpectedEvidence(
            "agent-claim-map",
            "fact",
            "final-answer",
            description="Scalar Evidence references selected for the final answer",
        ),
        ExpectedEvidence(
            "agent-final-answer", "artifact", "final-answer", artifact_role="final_answer"
        ),
    )
    plan = ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        task_id=task.task_id,
        workflow=task.workflow,
        workflow_version="0.1.0",
        objectives=(
            "Approve an LLM-generated constrained ExecutionPlan",
            "Execute only capability-authorized Geo Tools through the Harness",
            "Produce an evidence-bound final answer",
        ),
        steps=(
            PlanStep(
                "agent-runtime",
                "agent.general_runtime",
                {},
                expected_evidence_ids=(
                    "agent-plan",
                    "agent-evidence-index",
                    "agent-tool-artifacts",
                ),
                step_kind="agent",
            ),
            PlanStep(
                "final-answer",
                "artifact.final_answer",
                {},
                depends_on=("agent-runtime",),
                expected_evidence_ids=(
                    "agent-trace",
                    "agent-claim-map",
                    "agent-final-answer",
                ),
                step_kind="artifact",
            ),
        ),
        expected_evidence=evidence,
    )
    return task, plan


def _initial_user_message(request: str, asset: DataAsset | None) -> dict[str, Any]:
    if asset is None:
        source_context = (
            "本任务没有本地输入资产。只能使用已启用 Integration 提供的资产无关 Tool；"
            "项目名、对象名等用户文本不是已验证标识，必须通过 Tool 查询确认。"
        )
    else:
        source_context = (
            "Harness 已把唯一输入注册为 asset_id=primary。\n"
            "安全资产描述：" + json.dumps(asset.to_dict(disclose_path=False), ensure_ascii=False)
        )
    return {
        "role": "user",
        "content": f"用户任务：{request}\n{source_context}",
    }


def _phase_instruction(
    approved: ApprovedCandidatePlan | None,
    remaining_steps: list[str],
) -> str:
    if approved is None:
        return (
            "First call submit_execution_plan. Submit the smallest sufficient dependency-aware "
            "plan and do not provide a final answer yet."
        )
    if remaining_steps:
        return (
            "The candidate plan is approved. Call only eligible planned Tools whose dependencies "
            f"are complete. Remaining steps: {', '.join(remaining_steps)}. Each step succeeds once."
        )
    return (
        "All approved steps are complete. Immediately call submit_final_answer as one concise "
        "Tool Call. Select only the few scalar Evidence JSON Pointers needed for the answer. Keep "
        "interpretation under 600 Chinese characters, disclose capability limits, do not repeat "
        "raw Tool results, and never describe an unexecuted check as passed. Free text without "
        "submit_final_answer cannot complete the task."
    )


def _safe_tool_data(
    execution: ToolExecution, output_dir: Path, asset: DataAsset | None
) -> dict[str, Any]:
    def sanitize(value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            return {str(name): sanitize(item, str(name)) for name, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [sanitize(item, key) for item in value]
        if isinstance(value, str):
            if asset is not None and value == str(asset.source_path):
                return "asset://primary"
            if key == "workspace":
                return "workspace://explore"
            try:
                candidate = Path(value)
                if candidate.is_absolute():
                    resolved = candidate.resolve()
                    try:
                        relative = resolved.relative_to(output_dir.resolve())
                        return f"artifact://{relative.as_posix()}"
                    except ValueError:
                        return "[local path redacted]"
            except (OSError, ValueError):
                pass
        return value

    payload = sanitize(execution.result.data)
    assert isinstance(payload, dict)
    return payload


def _bound_tool_result(
    *,
    session: TaskRunSession,
    candidate_step_id: str,
    tool_name: str,
    data: dict[str, Any],
    max_bytes: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    encoded = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) <= max_bytes:
        return data, data
    path = session.output / "tool-results" / f"{candidate_step_id}-result.json"
    _atomic_json(
        path,
        {
            "schema_version": "1.0",
            "tool": tool_name,
            "data": data,
        },
    )
    artifact = session.register_planned_artifact(
        step_id="agent-runtime",
        evidence_id="agent-tool-artifacts",
        path=path,
        artifact_id=f"{candidate_step_id}-result-payload",
    )
    reference = {
        "result_storage": "artifact",
        "artifact_id": artifact.artifact_id,
        "artifact_sha256": artifact.sha256,
        "artifact_size_bytes": artifact.size_bytes,
        "original_json_bytes": len(encoded),
        "bounded_summary": _bounded_json_summary(data),
        "notice": "Full result is registered as an Artifact; this projection is not the full data.",
    }
    return reference, reference


def _bounded_json_summary(value: dict[str, Any]) -> dict[str, Any]:
    scalar_fields: dict[str, Any] = {}
    for key, item in value.items():
        if item is None or isinstance(item, (int, float, bool)):
            scalar_fields[str(key)] = item
        elif isinstance(item, str):
            encoded = item.encode("utf-8")
            scalar_fields[str(key)] = (
                item
                if len(encoded) <= 160
                else {
                    "type": "string",
                    "utf8_bytes": len(encoded),
                    "sha256": hashlib.sha256(encoded).hexdigest(),
                }
            )
    return {
        "top_level_keys": sorted(str(item) for item in value)[:64],
        "top_level_key_count": len(value),
        "scalar_fields": dict(list(sorted(scalar_fields.items()))[:24]),
    }


def _validation_value(data: dict[str, Any]) -> dict[str, Any] | None:
    analysis = data.get("analysis")
    if not isinstance(analysis, dict):
        return None
    validation = analysis.get("validation")
    return validation if isinstance(validation, dict) else None


def _register_tool_artifacts(
    session: TaskRunSession,
    candidate_step_id: str,
    execution: ToolExecution,
) -> None:
    paths = execution.result.data.get("artifacts")
    if not isinstance(paths, list):
        return
    for index, value in enumerate(paths, start=1):
        if not isinstance(value, str):
            raise GeneralAgentError("Tool returned a non-string artifact path")
        session.register_planned_artifact(
            step_id="agent-runtime",
            evidence_id="agent-tool-artifacts",
            path=value,
            artifact_id=f"{candidate_step_id}-artifact-{index:02d}",
        )


def _trace_payload(
    session: TaskRunSession,
    approved: ApprovedCandidatePlan,
    completed_steps: set[str],
    evidence_ids: list[str],
    turn: int,
) -> dict[str, Any]:
    agent_run = session.record.agent_runs[-1]
    return {
        "schema_version": "1.0",
        "task_id": session.task.task_id,
        "plan_id": approved.plan.plan_id,
        "completed_steps": sorted(completed_steps),
        "evidence_ids": evidence_ids,
        "model": {
            "provider": agent_run.provider,
            "name": agent_run.model,
            "protocol": agent_run.protocol,
            "turns": turn,
            "input_tokens": agent_run.input_tokens,
            "output_tokens": agent_run.output_tokens,
            "latency_ms": agent_run.model_latency_ms,
            "retry_count": agent_run.model_retry_count,
            "estimated_cost_usd": agent_run.estimated_cost_usd,
            "response_ids": list(agent_run.response_ids),
        },
        "tool_attempt_ids": list(agent_run.tool_attempt_ids),
        "context_snapshots": [
            {
                "snapshot_id": item.snapshot_id,
                "turn": item.turn,
                "policy_version": item.policy_version,
                "estimated_tokens": item.estimated_tokens,
                "context_sha256": item.context_sha256,
                "compaction_id": item.compaction_id,
            }
            for item in session.record.context_snapshots
            if item.agent_run_id == agent_run.agent_run_id
        ],
        "compactions": [item.to_dict() for item in session.record.compactions],
        "note": "No API key, authentication header, or full raw model response is stored.",
    }


def _finalize_submission(
    *,
    session: TaskRunSession,
    client: ModelClient,
    response: ModelResponse,
    agent_run_id: str,
    approved: ApprovedCandidatePlan,
    completed_steps: set[str],
    collected_evidence: list[str],
    tool_call_count: int,
    turn: int,
    submission: FinalAnswerSubmission,
) -> GeneralAgentResult:
    session.record_planned_evidence(
        step_id="agent-runtime",
        evidence_id="agent-evidence-index",
        value={
            "evidence_ids": collected_evidence,
            "plan_id": approved.plan.plan_id,
            "tool_attempt_count": tool_call_count,
        },
    )
    session.record_planned_evidence(
        step_id="final-answer",
        evidence_id="agent-claim-map",
        value={
            "claims": [item.to_dict() for item in submission.claims],
            "interpretation_is_evidence": False,
        },
    )
    trace_path = session.output / "agent-trace.json"
    _atomic_json(
        trace_path,
        _trace_payload(
            session,
            approved,
            completed_steps,
            collected_evidence,
            turn,
        ),
    )
    session.register_planned_artifact(
        step_id="final-answer",
        evidence_id="agent-trace",
        path=trace_path,
        artifact_id="agent-trace",
    )
    answer_path = _write_answer(
        session.output,
        interpretation=submission.interpretation,
        limitations=submission.limitations,
        claims=submission.claims,
        provider=client.provider,
        model=response.model,
        protocol=str(getattr(client, "protocol", "unknown")),
        turns=turn,
        task_id=session.task.task_id,
        evidence_ids=tuple(collected_evidence),
        plan_id=approved.plan.plan_id,
    )
    final_artifact = session.register_planned_artifact(
        step_id="final-answer",
        evidence_id="agent-final-answer",
        path=answer_path,
        artifact_id="agent-final-answer",
    )
    session.complete_agent_run(
        agent_run_id,
        status="completed",
        turns=turn,
        final_artifact_id=final_artifact.artifact_id,
    )
    result = session.finalize()
    if result.record.task_status not in {"completed", "completed_with_warnings"}:
        raise GeneralAgentError(
            f"Agent answer did not pass Harness validation: {result.record.task_status}"
        )
    return GeneralAgentResult(
        answer=submission.interpretation,
        provider=client.provider,
        model=response.model,
        protocol=str(getattr(client, "protocol", "unknown")),
        turns=turn,
        evidence_ids=tuple(collected_evidence),
        candidate_plan=approved,
        harness=result,
    )


def _write_answer(
    output_dir: Path,
    *,
    interpretation: str,
    limitations: tuple[str, ...],
    claims: tuple[EvidenceClaim, ...],
    provider: str,
    model: str,
    protocol: str,
    turns: int,
    task_id: str,
    evidence_ids: tuple[str, ...],
    plan_id: str,
) -> Path:
    path = output_dir / "agent-response.md"
    evidence = "\n".join(f"- `{item}`" for item in evidence_ids)
    facts = "\n".join(
        f"- `{item.claim_id}`: `{item.evidence_id}{item.selector}` = "
        f"`{json.dumps(item.value, ensure_ascii=False, allow_nan=False)}`"
        for item in claims
    )
    boundaries = "\n".join(f"- {item}" for item in limitations)
    path.write_text(
        "# GeoPhysAgent 回答\n\n"
        f"- Provider：`{provider}`\n"
        f"- Model：`{model}`\n"
        f"- Protocol：`{protocol}`\n"
        f"- Agent turns：{turns}\n"
        f"- Harness task：`{task_id}`\n"
        f"- Approved plan：`{plan_id}`\n\n"
        "## Evidence\n\n"
        "- `agent-evidence-index`\n"
        "- `agent-claim-map`\n"
        f"{evidence}\n\n"
        "## 已验证事实\n\n"
        f"{facts}\n\n"
        "## 模型解释（不是独立 Evidence）\n\n"
        f"{interpretation.strip()}\n\n"
        "## 能力边界\n\n"
        f"{boundaries}\n",
        encoding="utf-8",
    )
    return path


def _save_checkpoint(
    session: TaskRunSession,
    messages: list[dict[str, Any]],
    approved: ApprovedCandidatePlan | None,
    completed_steps: set[str],
    evidence_ids: list[str],
    turn: int,
    model_error: dict[str, Any] | None = None,
    compaction: CompactionRecord | None = None,
) -> None:
    prior = session.record.agent_checkpoint
    checkpoint = AgentCheckpoint(
        task_id=session.task.task_id,
        turn=turn,
        messages=tuple(json.loads(json.dumps(messages, ensure_ascii=False, allow_nan=False))),
        approved_plan=_candidate_submission(approved) if approved else None,
        approved_plan_id=approved.plan.plan_id if approved else None,
        completed_steps=tuple(sorted(completed_steps)),
        evidence_ids=tuple(dict.fromkeys(evidence_ids)),
        model_error=model_error,
        resume_policy="manual_only",
        compaction_id=(
            compaction.compaction_id
            if compaction is not None
            else prior.compaction_id
            if prior is not None
            else None
        ),
        context_snapshot_id=prior.context_snapshot_id if prior is not None else None,
    )
    session.record_agent_checkpoint(checkpoint, compaction=compaction)


def _candidate_submission(approved: ApprovedCandidatePlan) -> dict[str, Any]:
    return {
        "objective": approved.plan.objectives[0],
        "steps": [
            {
                "step_id": item.step_id,
                "tool": item.model_tool,
                "purpose": item.purpose,
                "arguments_hint": item.arguments_hint,
                "depends_on": list(item.depends_on),
            }
            for item in approved.steps
        ],
    }


def _candidate_submission_from_artifact(
    output_dir: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    path = output_dir / "candidate-plan.json"
    if not path.is_file():
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GeneralAgentError(f"Cannot recover candidate-plan artifact: {exc}") from exc
    if not isinstance(payload, dict):
        raise GeneralAgentError("candidate-plan artifact must contain an object")
    execution_plan = payload.get("execution_plan")
    agent_steps = payload.get("agent_steps")
    if not isinstance(execution_plan, dict) or not isinstance(agent_steps, list):
        raise GeneralAgentError("candidate-plan artifact has an invalid structure")
    objectives = execution_plan.get("objectives", [])
    if not isinstance(objectives, list) or not objectives:
        raise GeneralAgentError("candidate-plan artifact has no objective")
    steps: list[dict[str, Any]] = []
    for raw in agent_steps:
        if not isinstance(raw, dict):
            raise GeneralAgentError("candidate-plan artifact contains an invalid step")
        steps.append(
            {
                "step_id": raw.get("step_id"),
                "tool": raw.get("model_tool"),
                "purpose": raw.get("purpose"),
                "arguments_hint": raw.get("arguments_hint", {}),
                "depends_on": raw.get("depends_on", []),
            }
        )
    plan_id = execution_plan.get("plan_id")
    return (
        {"objective": str(objectives[0]), "steps": steps},
        str(plan_id) if plan_id else None,
    )


def _load_checkpoint(session: TaskRunSession) -> AgentCheckpoint:
    current = session.record.agent_checkpoint
    if current is not None:
        if current.schema_version != "2.0" or current.task_id != session.task.task_id:
            raise GeneralAgentError("Authoritative Agent checkpoint is invalid or unsupported")
        return current
    path = session.output / LEGACY_CHECKPOINT_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GeneralAgentError(f"Cannot read Agent checkpoint: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "1.0":
        raise GeneralAgentError("Agent checkpoint is invalid or unsupported")
    if not isinstance(payload.get("messages"), list):
        raise GeneralAgentError("Agent checkpoint has no valid message history")
    checkpoint = AgentCheckpoint(
        task_id=str(payload.get("task_id", "")),
        turn=int(payload.get("turn", 0)),
        messages=tuple(dict(item) for item in payload["messages"]),
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
        completed_steps=tuple(str(item) for item in payload.get("completed_steps", [])),
        evidence_ids=tuple(str(item) for item in payload.get("evidence_ids", [])),
        model_error=(
            dict(payload["model_error"]) if isinstance(payload.get("model_error"), dict) else None
        ),
        resume_policy=str(payload.get("resume_policy", "manual_only")),
    )
    if checkpoint.task_id != session.task.task_id:
        raise GeneralAgentError("Legacy Agent checkpoint belongs to another task")
    return session.record_agent_checkpoint(
        checkpoint,
        migration_source=LEGACY_CHECKPOINT_NAME,
    )


def _json_pointer(value: Any, selector: str) -> Any:
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


def _json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError) as exc:
        temporary.unlink(missing_ok=True)
        raise GeneralAgentError(f"Cannot persist JSON artifact: {exc}") from exc


def _tool_error(call_id: str, message: str) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": call_id,
        "is_error": True,
        "content": message[:1000],
    }
