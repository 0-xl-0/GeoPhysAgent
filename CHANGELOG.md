# Changelog

All notable changes are recorded here. Versions follow semantic versioning once a
public v0.1 release exists.

## Unreleased

### Open-source packaging (2026-11-02)

- Publish source snapshot under GPL-3.0-only with coordinated metadata and contributor guidance.
- Add offline quickstart and GitHub upload guide; include example model configuration in sdist.
- Select the direct MiniMax profile explicitly in the manual paid acceptance workflow.

### Added

- Named multi-provider model profiles in one TOML file, default and per-command
  profile selection, credential-safe profile listing, OpenCode Go defaults, and an
  OpenAI Chat Completions-compatible adapter alongside Anthropic Messages support.
- Stable OpenCode Go `x-opencode-session` routing bound to the Harness Task ID across
  model turns and manual resume, with header-injection validation.
- Engineering baseline for Windows CPython 3.11-3.14, locked development tools,
  shared quality commands, CI, security/contribution guidance, and reproducible
  sdist/wheel build procedure.
- LLM-first `geophysagent run` entry with capability-routed tools, structured
  candidate-plan approval, multi-tool turns, model streaming, bounded retries,
  Run Store checkpoints/resume, Evidence IDs, and planned final artifacts.
- Explicit paid real-model acceptance suite with selectable Profile, disabled unless
  the opt-in environment switch and that Profile's API key are both present.
- Versioned context contracts and a deterministic `ContextAssembler` with
  per-section/total budgets, metadata-only snapshots, atomic Tool Call/Result
  compaction, and a diagnostic forced-compaction option.
- Large Tool Result offloading to hashed run artifacts so model context receives
  only bounded summaries and references.
- Structured `submit_final_answer`, claim-to-Evidence JSON Pointer mapping, and a
  result validator that rejects missing, unresolved, changed, or non-scalar facts.
- Atomic Tool Provider lifecycle, immutable per-run Tool bindings, and a minimal MCP
  2025-11-25 stdio Client/Provider with manifest allowlists, paging, cancellation,
  bounded results, versioned RunRecord identity, and read-only integration examples.
- Local Integration Package install/list/enable/disable/remove commands with explicit
  file manifests, atomic registration, activation-time integrity verification, and no
  dependency downloads or install hooks.
- External-only Agent tasks with empty local asset sets and capability-safe Tool
  exposure.
- A reviewed-connector HTTP client for GET-only JSON APIs with exact origin allowlists,
  redirect rejection, bounded responses, and environment-injected credentials, plus a
  complete Software A MCP-to-API example.
- A LAS 2.0 reference application with wrapped input, encoding/delimiter variants,
  versioned aliases and QC Rule Pack, semantic report layers, deterministic SVG tracks,
  and provenance-rich figure metadata.
- A domain-neutral ReferenceSuite evaluator and CLI that compare completed Harness runs
  using plan, Tool, Evidence, Validator, Artifact, report and domain assertion layers;
  LAS, SEG-Y and external-only API regressions share the same result format.

### Changed

- AgentRun now records protocol, token usage, latency, response IDs, retry count,
  and available cost metadata without persisting credentials or raw responses.
- Agent messages, turn state, plan progress, compaction boundaries, and recovery
  reasons now use the versioned RunRecord checkpoint as their sole authority;
  legacy `agent-checkpoint.json` files are read-only migrated.
- Interrupted model runs and orphaned checkpoint revisions are reconciled without
  replaying an unconfirmed model call or overwriting the last committed revision.
- Streaming responses now preserve thinking/signature blocks, finalize Tool JSON
  only after the message stop reason is known, tolerate cumulative/overlapping
  compatible deltas, and classify output-limit truncation as a bounded retry.
- Agent turns expose only dependency-ready planned Tools; the final turn exposes
  only a compact `submit_final_answer` contract.
- General Agent Tool exposure is generated entirely from activated Tool Definitions;
  the static built-in Tool map and name-specific prompt branches were removed.

### Known limitations

- See `docs/KNOWN_LIMITATIONS.md`.
