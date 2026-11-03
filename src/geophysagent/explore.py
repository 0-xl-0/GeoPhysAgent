from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from geophysagent.domain import GeoToolResult
from geophysagent.providers import ToolContext, ToolDefinition, ToolExecution, ToolRegistryError
from geophysagent.tabular import load_tabular_asset


class ExplorePolicyError(ValueError):
    """Generated code violates the Explore policy."""


class ExploreExecutionError(RuntimeError):
    """Generated code passed policy checks but failed during execution."""

    def __init__(self, failure: str, workspace: Path):
        self.failure = failure
        self.workspace = workspace
        super().__init__(f"{failure}; workspace preserved at {workspace}")


@dataclass(frozen=True)
class ExplorePolicy:
    timeout_seconds: float = 10.0
    max_code_chars: int = 50_000
    max_input_bytes: int = 32 * 1024 * 1024
    max_output_bytes: int = 2 * 1024 * 1024
    max_console_chars: int = 64_000
    max_static_iterations: int = 1_000_000
    max_literal_repeat: int = 1_000_000
    max_power_exponent: int = 1_000
    policy_version: str = "0.1"


@dataclass(frozen=True)
class ExploreRun:
    run_id: str
    status: str
    purpose: str
    workspace: Path
    code_sha256: str
    input_sha256: str
    result: Any
    stdout: str
    stderr: str
    policy_version: str


_FORBIDDEN_NAMES = {
    "__builtins__",
    "breakpoint",
    "compile",
    "eval",
    "exec",
    "getattr",
    "globals",
    "help",
    "input",
    "locals",
    "memoryview",
    "object",
    "open",
    "setattr",
    "type",
    "vars",
}

_SAFE_BUILTINS = {
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "enumerate",
    "filter",
    "float",
    "int",
    "len",
    "list",
    "map",
    "max",
    "min",
    "range",
    "reversed",
    "round",
    "set",
    "sorted",
    "str",
    "sum",
    "tuple",
    "zip",
}


def validate_explore_code(code: str, policy: ExplorePolicy | None = None) -> None:
    selected = policy or ExplorePolicy()
    if not code.strip():
        raise ExplorePolicyError("Generated code is empty")
    if len(code) > selected.max_code_chars:
        raise ExplorePolicyError("Generated code exceeds the policy size limit")
    try:
        tree = ast.parse(code, filename="analysis.py", mode="exec")
    except SyntaxError as exc:
        raise ExplorePolicyError(f"Generated code has invalid syntax at line {exc.lineno}") from exc

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            violations.append(f"line {node.lineno}: imports are not allowed")
        elif isinstance(node, ast.While):
            violations.append(f"line {node.lineno}: while loops are not allowed")
        elif isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith)):
            violations.append(f"line {node.lineno}: classes and async constructs are not allowed")
        elif isinstance(node, ast.Name) and (
            node.id in _FORBIDDEN_NAMES or node.id.startswith("__")
        ):
            violations.append(f"line {node.lineno}: forbidden name {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            violations.append(f"line {node.lineno}: private/dunder attributes are not allowed")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "print":
                violations.append(
                    f"line {node.lineno}: print is not available; return observations through RESULT"
                )
            elif node.func.id in _FORBIDDEN_NAMES:
                violations.append(f"line {node.lineno}: forbidden call {node.func.id}")
            elif node.func.id == "range":
                iteration_count = _static_range_size(node)
                if iteration_count is not None and iteration_count > selected.max_static_iterations:
                    violations.append(
                        f"line {node.lineno}: range exceeds the static iteration limit"
                    )
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            repeat_count = _literal_repeat_count(node)
            if repeat_count is not None and repeat_count > selected.max_literal_repeat:
                violations.append(
                    f"line {node.lineno}: literal repetition exceeds the policy limit"
                )
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            exponent = _integer_constant(node.right)
            if exponent is not None and abs(exponent) > selected.max_power_exponent:
                violations.append(f"line {node.lineno}: power exponent exceeds the policy limit")
    if violations:
        raise ExplorePolicyError("; ".join(violations[:10]))


def validate_explore_data_access(code: str, columns: tuple[str, ...]) -> None:
    """Reject common misunderstandings of the stable Explore data contract."""
    try:
        tree = ast.parse(code, filename="analysis.py", mode="exec")
    except SyntaxError:
        return
    envelope_keys = {"asset", "columns", "rows", "row_count", "profile"}
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "DATA":
                violations.append(
                    f"line {node.lineno}: DATA is a dictionary, not an object; prefer ROWS or COLS"
                )
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            if node.value.id != "DATA":
                continue
            key = _string_constant(node.slice)
            if key is not None and key not in envelope_keys:
                hint = f'COLS["{key}"]' if key in columns else 'COLS["<column name>"]'
                violations.append(
                    f"line {node.lineno}: DATA does not expose columns at the top level; "
                    f"use {hint} or ROWS"
                )
        elif isinstance(node, ast.Subscript) and _is_data_profile(node.value):
            key = _string_constant(node.slice)
            if key in columns:
                violations.append(
                    f"line {node.lineno}: DATA['profile'] contains metadata, not column values; "
                    f'use COLS["{key}"]'
                )
    if violations:
        raise ExplorePolicyError("; ".join(violations[:10]))


def _string_constant(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_data_profile(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "DATA"
        and _string_constant(node.slice) == "profile"
    )


def _integer_constant(node: ast.AST) -> int | None:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    ):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _integer_constant(node.operand)
        if value is not None:
            return value if isinstance(node.op, ast.UAdd) else -value
    return None


def _static_range_size(node: ast.Call) -> int | None:
    if node.keywords or not 1 <= len(node.args) <= 3:
        return None
    values = [_integer_constant(argument) for argument in node.args]
    if any(value is None for value in values):
        return None
    integers = [int(value) for value in values if value is not None]
    try:
        return len(range(*integers))
    except ValueError:
        return None


def _literal_repeat_count(node: ast.BinOp) -> int | None:
    left_count = _integer_constant(node.left)
    right_count = _integer_constant(node.right)
    left_is_literal = isinstance(node.left, (ast.List, ast.Tuple, ast.Set)) or (
        isinstance(node.left, ast.Constant) and isinstance(node.left.value, (str, bytes))
    )
    right_is_literal = isinstance(node.right, (ast.List, ast.Tuple, ast.Set)) or (
        isinstance(node.right, ast.Constant) and isinstance(node.right.value, (str, bytes))
    )
    if left_count is not None and right_is_literal:
        return abs(left_count)
    if right_count is not None and left_is_literal:
        return abs(right_count)
    return None


def run_explore_code(
    *,
    data: dict[str, Any],
    code: str,
    purpose: str,
    output_dir: str | Path,
    input_sha256: str,
    policy: ExplorePolicy | None = None,
) -> ExploreRun:
    selected = policy or ExplorePolicy()
    validate_explore_code(code, selected)
    data_bytes = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data_bytes) > selected.max_input_bytes:
        raise ExplorePolicyError("Tabular input exceeds the Explore input size limit")
    run_id = str(uuid.uuid4())
    workspace = Path(output_dir).resolve() / "explore" / run_id
    workspace.mkdir(parents=True, exist_ok=False)
    code_path = workspace / "analysis.py"
    data_path = workspace / "data.json"
    runner_path = workspace / "_runner.py"
    result_path = workspace / "result.json"
    record_path = workspace / "run-record.json"
    code_path.write_text(code, encoding="utf-8")
    code_sha256 = hashlib.sha256(code_path.read_bytes()).hexdigest()
    data_path.write_bytes(data_bytes)
    runner_path.write_text(_runner_source(), encoding="utf-8")
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    }
    environment["PYTHONIOENCODING"] = "utf-8"

    status = "failed"
    result: Any = None
    stdout = ""
    stderr = ""
    failure: str | None = None
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-B", str(runner_path)],
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=selected.timeout_seconds,
            check=False,
        )
        stdout = completed.stdout[: selected.max_console_chars]
        stderr = completed.stderr[: selected.max_console_chars]
        if completed.returncode != 0:
            detail = _safe_execution_error(stderr)
            failure = f"Generated analysis exited with code {completed.returncode}"
            if detail:
                failure = f"{failure}: {detail}"
        elif not result_path.is_file():
            failure = "Generated analysis did not produce result.json"
        elif result_path.stat().st_size > selected.max_output_bytes:
            failure = "Generated analysis result exceeds the output size limit"
        else:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            status = "completed"
    except subprocess.TimeoutExpired:
        failure = f"Generated analysis exceeded {selected.timeout_seconds:g} seconds"
    except (OSError, json.JSONDecodeError) as exc:
        failure = f"Generated analysis produced an invalid result: {exc}"

    record = {
        "schema_version": "1.0",
        "run_id": run_id,
        "status": status,
        "purpose": purpose,
        "code_sha256": code_sha256,
        "input_sha256": input_sha256,
        "python_version": sys.version.split()[0],
        "policy": asdict(selected),
        "result_file": "result.json" if result_path.is_file() else None,
        "stdout_file": "stdout.txt",
        "stderr_file": "stderr.txt",
        "failure": failure,
    }
    (workspace / "stdout.txt").write_text(stdout, encoding="utf-8")
    (workspace / "stderr.txt").write_text(stderr, encoding="utf-8")
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    run = ExploreRun(
        run_id,
        status,
        purpose,
        workspace,
        code_sha256,
        input_sha256,
        result,
        stdout,
        stderr,
        selected.policy_version,
    )
    if failure:
        raise ExploreExecutionError(failure, workspace)
    return run


def _safe_execution_error(stderr: str) -> str | None:
    """Return a useful final exception line without exposing traceback paths."""
    allowed = {
        "AttributeError",
        "IndexError",
        "KeyError",
        "NameError",
        "OverflowError",
        "RuntimeError",
        "StatisticsError",
        "SyntaxError",
        "TypeError",
        "ValueError",
        "ZeroDivisionError",
    }
    for line in reversed(stderr.splitlines()):
        candidate = line.strip()
        match = re.match(r"^([A-Za-z][A-Za-z0-9_]*Error):\s*(.*)$", candidate)
        if match and match.group(1) in allowed:
            detail = re.sub(r"[A-Za-z]:\\[^\r\n]+", "[local path redacted]", match.group(2))
            return f"{match.group(1)}: {detail[:300]}"
    return None


class ExplorePythonProvider:
    provider_id = "explore-python"

    def __init__(self, policy: ExplorePolicy | None = None):
        self._policy = policy or ExplorePolicy()
        self._definitions = tuple(
            ToolDefinition(
                name=name,
                version="0.1.0",
                description="在受策略限制的独立工作区运行无 I/O、无 import 的 Python 数据计算代码。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "asset_id": {"type": "string"},
                        "purpose": {"type": "string"},
                        "code": {"type": "string"},
                    },
                    "required": ["asset_id", "purpose", "code"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "string"},
                        "code_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                        "result": {},
                        "workspace": {"type": "string"},
                        "verification": {"const": "exploratory"},
                        "artifacts": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 3,
                        },
                    },
                    "required": [
                        "run_id",
                        "code_sha256",
                        "result",
                        "workspace",
                        "verification",
                        "artifacts",
                    ],
                    "additionalProperties": False,
                },
                risk="code_execution",
                read_only=False,
                idempotent=False,
                replay_policy="manual",
                model_name="run_python_analysis" if name == "explore.python" else None,
                purpose="在隔离工作区补足尚未产品化的表格分析能力。",
                usage_guidance=(
                    "代码只能使用 ROWS、COLS、COLUMN_NAMES、DATA、math、statistics，"
                    "禁止 import、I/O、网络，并必须赋值 JSON 可序列化的 RESULT。"
                ),
                input_capabilities=("explore.python",),
                side_effects=("在任务目录创建探索代码和结果 Artifact",),
                expected_evidence_types=("tool_result", "artifact"),
                expected_artifact_roles=("supporting_result",),
            )
            for name in ("explore.run_python", "explore.python")
        )

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self._definitions

    def execute(self, name: str, arguments: dict[str, Any], context: ToolContext) -> ToolExecution:
        if name not in {item.name for item in self._definitions}:
            raise ToolRegistryError(f"Unknown Explore tool: {name}")
        asset_id = arguments["asset_id"]
        asset = context.assets.get(asset_id)
        if asset is None:
            raise ToolRegistryError(f"Asset is not authorized: {asset_id}")
        dataset = load_tabular_asset(asset, null_values=[-999.25])
        validate_explore_data_access(arguments["code"], dataset.columns)
        run = run_explore_code(
            data=dataset.to_model_data(),
            code=arguments["code"],
            purpose=arguments["purpose"],
            output_dir=context.output_dir,
            input_sha256=asset.sha256,
            policy=self._policy,
        )
        result = GeoToolResult(
            tool_name=name,
            tool_version="0.1.0",
            status="success",
            provider=self.provider_id,
            data={
                "run_id": run.run_id,
                "code_sha256": run.code_sha256,
                "result": run.result,
                "workspace": str(run.workspace),
                "verification": "exploratory",
                "artifacts": [
                    str(run.workspace / "analysis.py"),
                    str(run.workspace / "result.json"),
                    str(run.workspace / "run-record.json"),
                ],
            },
        )
        return ToolExecution(result, run)


def _runner_source() -> str:
    safe_names = ",\n        ".join(f'"{name}": {name}' for name in sorted(_SAFE_BUILTINS))
    return f"""import json
import math
import statistics

with open("data.json", "r", encoding="utf-8") as handle:
    DATA = json.load(handle)
ROWS = DATA["rows"]
COLUMN_NAMES = DATA["columns"]
COLS = {{name: [row.get(name) for row in ROWS] for name in COLUMN_NAMES}}
with open("analysis.py", "r", encoding="utf-8") as handle:
    SOURCE = handle.read()

SAFE_BUILTINS = {{
        {safe_names}
}}
scope = {{
    "__builtins__": SAFE_BUILTINS,
    "DATA": DATA,
    "ROWS": ROWS,
    "COLS": COLS,
    "COLUMN_NAMES": COLUMN_NAMES,
    "math": math,
    "statistics": statistics,
}}
exec(compile(SOURCE, "analysis.py", "exec"), scope, scope)
if "RESULT" not in scope:
    raise RuntimeError("Generated code must assign a JSON-serializable value to RESULT")
with open("result.json", "w", encoding="utf-8") as handle:
    json.dump(scope["RESULT"], handle, ensure_ascii=False, separators=(",", ":"))
"""
