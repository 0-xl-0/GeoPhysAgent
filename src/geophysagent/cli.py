from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from geophysagent.agent import AgentRunError, run_las_agent, write_agent_answer
from geophysagent.application import (
    execute_las_qc_task,
    execute_segy_inspection_task,
    execute_table_pipeline_task,
)
from geophysagent.asset_adapters import (
    EXPLORE_PYTHON,
    TABULAR_INSPECT,
    WELL_LOG_QC,
    SEGY_INSPECT,
    AssetAdapterError,
    get_default_asset_adapter_registry,
)
from geophysagent.assets import AssetError, register_asset
from geophysagent.config import (
    ConfigurationError,
    load_model_configuration,
    load_model_profile,
)
from geophysagent.explore import ExploreExecutionError, ExplorePolicyError
from geophysagent.explore_agent import ExploreAgentError, run_explore_agent, write_explore_answer
from geophysagent.harness import GeoDomainHarness, HarnessError, cancel_task_directory
from geophysagent.integrations import IntegrationPackageError, IntegrationPackageManager
from geophysagent.las import LasParseError
from geophysagent.model import ModelClientError, create_model_client
from geophysagent.pipeline import PipelineError, load_pipeline_spec
from geophysagent.promotion import PromotionError, promote_explore_run
from geophysagent.providers import ToolRegistryError
from geophysagent.run_store import RunStoreError
from geophysagent.run_inspection import validate_run_directory
from geophysagent.reference_eval import (
    ReferenceEvaluationError,
    evaluate_reference_suite,
    load_reference_suite,
    write_reference_evaluation,
)
from geophysagent.builtin_provider import create_builtin_registry
from geophysagent.segy import SegyError
from geophysagent.tabular import TabularError, inspect_tabular_asset
from geophysagent.general_agent import (
    GeneralAgentError,
    create_waiting_general_agent_run,
    resume_general_agent,
    run_general_agent,
    write_general_agent_answer,
    create_general_agent_registry,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="geophysagent")
    subcommands = parser.add_subparsers(dest="command", required=True)
    analyze = subcommands.add_parser("analyze", help="Analyze and validate a LAS file")
    analyze.add_argument("input", type=Path, help="Path to the LAS file")
    analyze.add_argument("--output", type=Path, required=True, help="Output directory")
    inspect = subcommands.add_parser(
        "inspect", help="Identify an asset and its implemented capabilities"
    )
    inspect.add_argument("input", type=Path, help="Path to the data file")
    preprocess = subcommands.add_parser(
        "preprocess", help="Run a parameterized preprocessing pipeline"
    )
    preprocess.add_argument("input", type=Path, help="Path to the data file")
    preprocess.add_argument("--pipeline", type=Path, required=True, help="Pipeline JSON file")
    preprocess.add_argument("--output", type=Path, required=True, help="Output directory")
    segy = subcommands.add_parser("segy", help="Inspect and QC a SEG-Y file without modifying it")
    segy.add_argument("input", type=Path, help="Path to the SEG-Y file")
    segy.add_argument("--output", type=Path, required=True, help="Output directory")
    agent = subcommands.add_parser("agent", help="Run the constrained LLM LAS workflow")
    agent.add_argument("input", type=Path, help="Path to the LAS file")
    agent.add_argument("--request", required=True, help="Natural-language task")
    agent.add_argument("--output", type=Path, required=True, help="Output directory")
    agent.add_argument("--config", type=Path, default=Path("geophysagent.toml"))
    agent.add_argument("--profile", help="Named model profile (defaults to models.default)")
    doctor = subcommands.add_parser("doctor", help="Validate model configuration")
    doctor.add_argument("--config", type=Path, default=Path("geophysagent.toml"))
    doctor.add_argument("--profile", help="Named model profile (defaults to models.default)")
    models = subcommands.add_parser("models", help="List configured model profiles")
    models.add_argument("--config", type=Path, default=Path("geophysagent.toml"))
    models.add_argument("--json", action="store_true", help="Print structured JSON")
    explore = subcommands.add_parser(
        "explore", help="Let the LLM generate controlled analysis code"
    )
    explore.add_argument("input", type=Path, help="Path to a LAS/TXT/CSV/TSV file")
    explore.add_argument("--request", required=True, help="Exploratory analysis request")
    explore.add_argument("--output", type=Path, required=True, help="Output directory")
    explore.add_argument("--config", type=Path, default=Path("geophysagent.toml"))
    explore.add_argument("--profile", help="Named model profile (defaults to models.default)")
    run = subcommands.add_parser(
        "run", help="Run the LLM-first capability-routed GeoPhysAgent workflow"
    )
    run.add_argument("input", type=Path, nargs="?", help="Path to the authorized data asset")
    run.add_argument("--request", help="Natural-language task")
    run.add_argument("--output", type=Path, required=True, help="Task output directory")
    run.add_argument("--config", type=Path, default=Path("geophysagent.toml"))
    run.add_argument("--profile", help="Named model profile (defaults to models.default)")
    run.add_argument(
        "--mcp-manifest",
        type=Path,
        action="append",
        default=[],
        help="Activate one reviewed local MCP stdio Integration Manifest (repeatable)",
    )
    run.add_argument(
        "--integration-root",
        type=Path,
        help="Installed Integration registry root (defaults to the user data directory)",
    )
    run.add_argument(
        "--integration",
        dest="selected_integrations",
        action="append",
        default=[],
        help="Use only this enabled installed Integration (repeatable)",
    )
    run.add_argument(
        "--no-installed-integrations",
        action="store_true",
        help="Do not activate enabled installed Integrations for this run",
    )
    run.add_argument(
        "--resume",
        action="store_true",
        help="Resume this output directory from its authoritative Run Store checkpoint",
    )
    run.add_argument(
        "--compact-context",
        action="store_true",
        help="Request one diagnostic context compaction at the next safe model boundary",
    )
    promote = subcommands.add_parser(
        "promote", help="Copy an Explore run into an inactive tool candidate"
    )
    promote.add_argument("run", type=Path, help="Explore workspace containing run-record.json")
    promote.add_argument("--name", required=True, help="Candidate tool name")
    promote.add_argument(
        "--destination", type=Path, default=Path("candidate-tools"), help="Candidate root directory"
    )
    status = subcommands.add_parser("status", help="Read authoritative task status and timeline")
    status.add_argument("run", type=Path, help="Harness run directory")
    status.add_argument("--json", action="store_true", help="Print structured JSON")
    resume = subcommands.add_parser("resume", help="Resume a non-terminal Harness task")
    resume.add_argument("run", type=Path, help="Harness run directory")
    cancel = subcommands.add_parser("cancel", help="Cancel or request cancellation of a task")
    cancel.add_argument("run", type=Path, help="Harness run directory")
    cancel.add_argument("--reason", default="user requested cancellation")
    validate = subcommands.add_parser(
        "validate", help="Verify a run, timeline, inputs and artifacts"
    )
    validate.add_argument("run", type=Path, help="Harness run directory")
    validate.add_argument("--json", action="store_true", help="Print structured JSON")
    evaluate = subcommands.add_parser(
        "evaluate", help="Evaluate recorded Harness runs against a reference suite"
    )
    evaluate.add_argument("--suite", type=Path, required=True, help="Reference suite JSON")
    evaluate.add_argument(
        "--runs-root", type=Path, required=True, help="Root containing case run directories"
    )
    evaluate.add_argument("--output", type=Path, required=True, help="Evaluation JSON output")
    integration = subcommands.add_parser(
        "integration", help="Install and manage reviewed local Integration Packages"
    )
    integration_actions = integration.add_subparsers(dest="integration_action", required=True)
    install = integration_actions.add_parser("install", help="Install a local package directory")
    install.add_argument("source", type=Path, help="Directory containing package.json")
    install.add_argument("--disabled", action="store_true", help="Install without enabling")
    install.add_argument("--root", type=Path, help="Integration registry root")
    listing = integration_actions.add_parser("list", help="List installed Integrations")
    listing.add_argument("--json", action="store_true", help="Print structured JSON")
    listing.add_argument("--root", type=Path, help="Integration registry root")
    for action in ("enable", "disable", "remove"):
        command = integration_actions.add_parser(action, help=f"{action.title()} an Integration")
        command.add_argument("package_id", help="Installed Integration package id")
        command.add_argument("--root", type=Path, help="Integration registry root")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "models":
        try:
            configuration = load_model_configuration(args.config)
        except ConfigurationError as exc:
            print(f"Configuration invalid: {exc}", file=sys.stderr)
            return 2
        entries = []
        for name in sorted(configuration.profiles):
            profile = configuration.profiles[name]
            entries.append(
                {
                    "name": name,
                    "default": name == configuration.default_profile,
                    "provider": profile.provider,
                    "model": profile.model,
                    "protocol": profile.protocol,
                    "base_url": profile.base_url,
                    "api_key_env": profile.api_key_env,
                    "credential_configured": bool(os.environ.get(profile.api_key_env, "").strip()),
                }
            )
        if args.json:
            print(
                json.dumps(
                    {
                        "default_profile": configuration.default_profile,
                        "legacy_format": configuration.legacy_format,
                        "profiles": entries,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(f"Default model profile: {configuration.default_profile}")
            if configuration.legacy_format:
                print("Format: legacy [model] (migration recommended)")
            for entry in entries:
                marker = "*" if entry["default"] else " "
                credential = "configured" if entry["credential_configured"] else "missing"
                print(
                    f"{marker} {entry['name']}: {entry['provider']} / {entry['model']} "
                    f"[{entry['protocol']}; credential {credential}: {entry['api_key_env']}]"
                )
        return 0
    if args.command == "evaluate":
        try:
            suite = load_reference_suite(args.suite)
            evaluation = evaluate_reference_suite(suite, args.runs_root)
            output = write_reference_evaluation(evaluation, args.output)
        except (ReferenceEvaluationError, RunStoreError, OSError) as exc:
            print(f"Reference evaluation failed: {exc}", file=sys.stderr)
            return 2
        print(f"Suite: {evaluation.suite_id}@{evaluation.suite_version}")
        print(f"Cases: {evaluation.passed_case_count}/{evaluation.case_count} passed")
        print(
            "Rates: "
            f"plan={evaluation.metrics['plan_valid_rate']:.1%}, "
            f"tool={evaluation.metrics['mean_tool_success_rate']:.1%}, "
            f"evidence={evaluation.metrics['mean_evidence_completeness']:.1%}, "
            f"validator={evaluation.metrics['mean_validator_nonfailure_rate']:.1%}, "
            f"artifact={evaluation.metrics['mean_artifact_completeness']:.1%}, "
            f"task={evaluation.metrics['task_success_rate']:.1%}"
        )
        print(f"Evaluation: {output}")
        return 0 if evaluation.passed else 1
    if args.command == "integration":
        try:
            manager = IntegrationPackageManager(args.root)
            if args.integration_action == "install":
                record = manager.install(args.source, enabled=not args.disabled)
                print(f"Installed Integration: {record.package_id}@{record.version}")
                print(f"Status: {'enabled' if record.enabled else 'disabled'}")
                return 0
            if args.integration_action == "list":
                records = manager.list()
                if args.json:
                    print(
                        json.dumps(
                            {
                                "schema_version": "1.0",
                                "integrations": [r.to_dict() for r in records],
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                elif not records:
                    print("No Integrations installed")
                else:
                    for record in records:
                        state = "enabled" if record.enabled else "disabled"
                        print(f"{record.package_id}@{record.version} [{state}]")
                return 0
            if args.integration_action in {"enable", "disable"}:
                record = manager.set_enabled(
                    args.package_id, enabled=args.integration_action == "enable"
                )
                print(
                    f"Integration {record.package_id}: "
                    f"{'enabled' if record.enabled else 'disabled'}"
                )
                return 0
            record = manager.remove(args.package_id)
            print(f"Removed Integration: {record.package_id}@{record.version}")
            return 0
        except (IntegrationPackageError, OSError) as exc:
            print(f"Integration command failed: {exc}", file=sys.stderr)
            return 2
    if args.command == "analyze":
        try:
            asset = register_asset(args.input)
            result, harness_result = execute_las_qc_task(
                asset=asset,
                request="Run deterministic LAS quality control and basic statistics",
                output_dir=args.output,
            )
            json_path = args.output.resolve() / "result.json"
            report_path = args.output.resolve() / "report.md"
        except (
            AssetError,
            AssetAdapterError,
            HarnessError,
            RunStoreError,
            LasParseError,
            OSError,
        ) as exc:
            print(f"Analysis failed: {exc}", file=sys.stderr)
            return 2
        print(f"Status: {harness_result.record.task_status}")
        print(f"Task: {harness_result.record.task_id}")
        print(f"Result: {json_path}")
        print(f"Report: {report_path}")
        print(f"Figure: {args.output.resolve() / 'well-log.svg'}")
        print(f"Figure metadata: {args.output.resolve() / 'well-log.figure.json'}")
        return 1 if result.status == "failed" else 0
    if args.command == "inspect":
        try:
            asset = register_asset(args.input)
            profile = (
                inspect_tabular_asset(asset) if TABULAR_INSPECT in asset.capabilities else None
            )
        except (AssetError, AssetAdapterError, TabularError, LasParseError, OSError) as exc:
            print(f"Inspection failed: {exc}", file=sys.stderr)
            return 2
        print(
            json.dumps(
                {
                    "asset": asset.to_dict(),
                    "implemented_capabilities": list(asset.capabilities),
                    "profile": profile.to_dict() if profile is not None else None,
                    "note": (
                        None
                        if profile is not None
                        else (
                            "Non-tabular SEG-Y asset; run 'geophysagent segy <file> --output <dir>'."
                            if SEGY_INSPECT in asset.capabilities
                            else "Asset is recognized, but no domain parser is implemented yet."
                        )
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "segy":
        try:
            asset = register_asset(args.input)
            result, harness_result = execute_segy_inspection_task(
                asset=asset,
                request="Inspect SEG-Y structure, geometry, amplitudes and basic trace quality",
                output_dir=args.output,
            )
        except (
            AssetError,
            AssetAdapterError,
            SegyError,
            ToolRegistryError,
            HarnessError,
            RunStoreError,
            OSError,
        ) as exc:
            print(f"SEG-Y inspection failed: {exc}", file=sys.stderr)
            return 2
        print(f"Status: {harness_result.record.task_status}")
        print(f"Task: {harness_result.record.task_id}")
        print(f"Result: {harness_result.output_dir / 'segy-result.json'}")
        print(f"Report: {harness_result.output_dir / 'segy-report.md'}")
        return 1 if result.status == "failed" else 0
    if args.command == "preprocess":
        try:
            asset = register_asset(args.input)
            pipeline = load_pipeline_spec(args.pipeline)
            processed, harness_result = execute_table_pipeline_task(
                asset=asset,
                pipeline=pipeline,
                request="Execute the user-supplied parameterized preprocessing pipeline",
                output_dir=args.output,
            )
        except (
            AssetError,
            AssetAdapterError,
            TabularError,
            PipelineError,
            ToolRegistryError,
            HarnessError,
            RunStoreError,
            LasParseError,
            OSError,
        ) as exc:
            print(f"Preprocessing failed: {exc}", file=sys.stderr)
            return 2
        print(f"Status: {harness_result.record.task_status}")
        print(f"Task: {harness_result.record.task_id}")
        for artifact in harness_result.record.artifacts:
            print(f"Artifact: {harness_result.output_dir / artifact.relative_path}")
        return 0
    if args.command == "doctor":
        try:
            profile = load_model_profile(args.config, args.profile)
            profile.resolve_api_key()
        except ConfigurationError as exc:
            print(f"Configuration invalid: {exc}", file=sys.stderr)
            return 2
        print("Model configuration is valid")
        print(f"Profile: {profile.profile_name}")
        print(f"Provider: {profile.provider}")
        print(f"Model: {profile.model}")
        print(f"Protocol: {profile.protocol}")
        print(f"Credential source: {profile.api_key_env}")
        return 0
    if args.command == "run":
        if args.resume and (args.input is not None or args.request):
            print(
                "Run resume failed: do not pass input or --request with --resume", file=sys.stderr
            )
            return 2
        if not args.resume and not args.request:
            print("Run failed: --request is required for a new task", file=sys.stderr)
            return 2
        if args.no_installed_integrations and args.selected_integrations:
            print(
                "Run failed: --integration cannot be combined with --no-installed-integrations",
                file=sys.stderr,
            )
            return 2
        try:
            profile = load_model_profile(args.config, args.profile)
            installed_manifests: tuple[Path, ...] = ()
            if not args.no_installed_integrations:
                installed_manifests = IntegrationPackageManager(
                    args.integration_root
                ).enabled_manifest_paths(tuple(args.selected_integrations) or None)
            manifests = (*installed_manifests, *tuple(args.mcp_manifest))
            registry = create_general_agent_registry(manifests)
            try:
                api_key = profile.resolve_api_key()
            except ConfigurationError as exc:
                if not args.resume:
                    task_id = create_waiting_general_agent_run(
                        user_request=args.request,
                        source_path=args.input,
                        output_dir=args.output,
                        provider=profile.provider,
                        model=profile.model,
                        protocol=profile.protocol,
                        reason=str(exc),
                        registry=registry,
                    )
                    print(
                        f"Run waiting for model configuration; Task: {task_id}; "
                        f"resume with the same --output after fixing: {exc}",
                        file=sys.stderr,
                    )
                    return 2
                registry.dispose()
                raise
            client = create_model_client(profile, api_key)
            result = (
                resume_general_agent(
                    client=client,
                    output_dir=args.output,
                    registry=registry,
                    force_context_compaction=args.compact_context,
                )
                if args.resume
                else run_general_agent(
                    client=client,
                    user_request=args.request,
                    source_path=args.input,
                    output_dir=args.output,
                    registry=registry,
                    force_context_compaction=args.compact_context,
                )
            )
            answer_path = write_general_agent_answer(result, args.output)
        except (
            ConfigurationError,
            ModelClientError,
            GeneralAgentError,
            AssetError,
            AssetAdapterError,
            ExplorePolicyError,
            ExploreExecutionError,
            TabularError,
            ToolRegistryError,
            IntegrationPackageError,
            HarnessError,
            RunStoreError,
            LasParseError,
            SegyError,
            OSError,
        ) as exc:
            print(f"Run failed: {exc}", file=sys.stderr)
            return 2
        agent_run = result.harness.record.agent_runs[-1]
        print(f"Status: {result.harness.record.task_status}")
        print(f"Task: {result.harness.record.task_id}")
        print(f"Provider: {result.provider}")
        print(f"Model: {result.model}")
        print(f"Protocol: {result.protocol}")
        print(
            "Usage: "
            f"input_tokens={agent_run.input_tokens}, "
            f"output_tokens={agent_run.output_tokens}, "
            f"latency_ms={agent_run.model_latency_ms}, "
            f"estimated_cost_usd={agent_run.estimated_cost_usd}"
        )
        print(f"Answer: {answer_path}")
        return 0
    if args.command == "agent":
        try:
            preflight_asset = register_asset(args.input)
            get_default_asset_adapter_registry().require_capability(preflight_asset, WELL_LOG_QC)
            profile = load_model_profile(args.config, args.profile)
            client = create_model_client(profile)
            result = run_las_agent(
                client=client,
                user_request=args.request,
                source_path=args.input,
                output_dir=args.output,
            )
            answer_path = write_agent_answer(result, args.output)
        except (
            ConfigurationError,
            ModelClientError,
            AgentRunError,
            AssetError,
            AssetAdapterError,
            HarnessError,
            RunStoreError,
            ToolRegistryError,
            LasParseError,
            OSError,
        ) as exc:
            print(f"Agent failed: {exc}", file=sys.stderr)
            return 2
        print(f"Status: {result.harness.record.task_status}")
        print(f"Execution: {result.harness.record.execution_status}")
        print(f"Task: {result.harness.record.task_id}")
        print(f"Provider: {result.provider}")
        print(f"Model: {result.model}")
        print(f"Answer: {answer_path}")
        return (
            0
            if result.harness.record.task_status in {"completed", "completed_with_warnings"}
            else 1
        )
    if args.command == "explore":
        try:
            preflight_asset = register_asset(args.input)
            get_default_asset_adapter_registry().require_capability(preflight_asset, EXPLORE_PYTHON)
            profile = load_model_profile(args.config, args.profile)
            client = create_model_client(profile)
            result = run_explore_agent(
                client=client,
                user_request=args.request,
                source_path=args.input,
                output_dir=args.output,
            )
            answer_path = write_explore_answer(result, args.output)
        except (
            ConfigurationError,
            ModelClientError,
            ExploreAgentError,
            ExplorePolicyError,
            ExploreExecutionError,
            AssetError,
            AssetAdapterError,
            TabularError,
            ToolRegistryError,
            HarnessError,
            RunStoreError,
            LasParseError,
            OSError,
        ) as exc:
            print(f"Explore failed: {exc}", file=sys.stderr)
            return 2
        print(f"Status: {result.harness.record.task_status} (exploratory)")
        print(f"Execution: {result.harness.record.execution_status}")
        print(f"Task: {result.harness.record.task_id}")
        print(f"Provider: {result.provider}")
        print(f"Model: {result.model}")
        print(f"Workspace: {result.run.workspace}")
        print(f"Answer: {answer_path}")
        return (
            0
            if result.harness.record.task_status in {"completed", "completed_with_warnings"}
            else 1
        )
    if args.command == "promote":
        try:
            target = promote_explore_run(args.run, name=args.name, destination=args.destination)
        except (PromotionError, OSError) as exc:
            print(f"Promotion failed: {exc}", file=sys.stderr)
            return 2
        print("Candidate created but not activated")
        print(f"Candidate: {target}")
        return 0
    if args.command == "status":
        inspection = validate_run_directory(args.run)
        if args.json:
            print(json.dumps(inspection.to_dict(), ensure_ascii=False, indent=2))
        else:
            if inspection.task_id is None:
                print("Status failed: run is unreadable", file=sys.stderr)
            else:
                print(f"Task: {inspection.task_id}")
                print(f"Task status: {inspection.task_status}")
                print(f"Execution status: {inspection.execution_status}")
                print(f"Checkpoint: {inspection.snapshot_revision}")
                print(f"Timeline entries: {len(inspection.timeline)}")
                print(f"Integrity: {'valid' if inspection.valid else 'invalid'}")
                for issue in inspection.issues:
                    print(f"{issue.severity.upper()} {issue.code}: {issue.message}")
        return 0 if inspection.task_id is not None else 2
    if args.command == "resume":
        try:
            result = GeoDomainHarness(create_builtin_registry()).resume(args.run)
        except (HarnessError, RunStoreError, AssetError, OSError) as exc:
            print(f"Resume failed: {exc}", file=sys.stderr)
            return 2
        print(f"Task: {result.record.task_id}")
        print(f"Task status: {result.record.task_status}")
        print(f"Execution status: {result.record.execution_status}")
        return (
            0
            if result.record.task_status
            in {"completed", "completed_with_warnings", "waiting_for_user"}
            else 1
        )
    if args.command == "cancel":
        try:
            outcome, record = cancel_task_directory(args.run, reason=args.reason)
        except (HarnessError, RunStoreError, OSError) as exc:
            print(f"Cancel failed: {exc}", file=sys.stderr)
            return 2
        print(f"Cancellation: {outcome}")
        print(f"Task: {record.task_id}")
        print(f"Task status: {record.task_status}")
        return 0
    if args.command == "validate":
        inspection = validate_run_directory(args.run)
        if args.json:
            print(json.dumps(inspection.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"Run integrity: {'valid' if inspection.valid else 'invalid'}")
            if inspection.task_id:
                print(f"Task: {inspection.task_id}")
                print(f"Status: {inspection.task_status}/{inspection.execution_status}")
            for issue in inspection.issues:
                print(f"{issue.severity.upper()} {issue.code}: {issue.message}")
        return 0 if inspection.valid else 1
    return 2
