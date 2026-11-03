from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path


class PromotionError(ValueError):
    """An Explore run is not eligible to become a candidate tool."""


def promote_explore_run(
    run_dir: str | Path,
    *,
    name: str,
    destination: str | Path,
) -> Path:
    source = Path(run_dir).resolve()
    slug = name.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,62}", slug):
        raise PromotionError("Candidate name must match [a-z][a-z0-9-]{1,62}")
    record_path = source / "run-record.json"
    code_path = source / "analysis.py"
    if not record_path.is_file() or not code_path.is_file():
        raise PromotionError("Explore workspace is missing run-record.json or analysis.py")
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionError(f"Cannot read Explore run record: {exc}") from exc
    if record.get("status") != "completed":
        raise PromotionError("Only completed Explore runs can be promoted")
    code = code_path.read_bytes()
    code_hash = hashlib.sha256(code).hexdigest()
    if code_hash != record.get("code_sha256"):
        raise PromotionError("Explore code hash no longer matches the run record")
    target = Path(destination).resolve() / slug
    if target.exists():
        raise PromotionError(f"Candidate destination already exists: {target}")
    target.mkdir(parents=True)
    shutil.copy2(code_path, target / "analysis.py")
    evidence = target / "evidence"
    evidence.mkdir()
    shutil.copy2(record_path, evidence / "source-run-record.json")
    result_path = source / "result.json"
    if result_path.is_file():
        shutil.copy2(result_path, evidence / "example-result.json")
    required_checks = [
        "human_code_review",
        "tool_input_output_schema",
        "unit_tests",
        "golden_data_tests",
        "domain_validator",
        "dependency_review",
    ]
    manifest = {
        "schema_version": "1.0",
        "name": slug,
        "status": "pending_review",
        "active": False,
        "purpose": record.get("purpose"),
        "source_run_id": record.get("run_id"),
        "source_code_sha256": code_hash,
        "source_input_sha256": record.get("input_sha256"),
        "required_before_activation": required_checks,
    }
    (target / "candidate-tool.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (target / "review-checklist.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "decision": "pending",
                "reviewer": None,
                "checks": {check: False for check in required_checks},
                "notes": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (target / "tool-contract.example.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "tool_name": f"geo.{slug.replace('-', '_')}",
                "version": "0.1.0",
                "provider": "builtin-or-mcp",
                "input_schema": {"type": "object", "properties": {}},
                "output_schema": {"type": "object", "properties": {}},
                "validator": "replace-with-validator-id",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (target / "README.md").write_text(
        f"# Candidate Tool: {slug}\n\n"
        "This candidate was promoted from an Explore run and is **not active**.\n\n"
        "Formalization flow:\n\n"
        "1. Review `analysis.py` and the preserved evidence.\n"
        "2. Replace the example contract with reviewed input/output schemas.\n"
        "3. Refactor the code into a deterministic Built-in Tool or an MCP Server Tool.\n"
        "4. Add unit, golden-data and failure tests plus a domain Validator.\n"
        "5. Complete `review-checklist.json`; registration remains an explicit developer change.\n\n"
        "Copying this directory never registers or activates code.\n",
        encoding="utf-8",
    )
    return target
