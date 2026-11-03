# Contributing

GeoPhysAgent v0.1 is Python-only and licensed under GPL-3.0-only. By submitting
a contribution, you agree to license it under the same terms. Submit only code
you have the right to contribute, and preserve third-party attribution.

## Development baseline

1. Use CPython 3.11, 3.12, 3.13, or 3.14 on Windows.
2. Create and activate `.venv`.
3. Run `python -m pip install -r requirements-dev.lock`.
4. Run `python -m pip install --no-deps -e .`.
5. Run `./scripts/quality.ps1` before review.

Use `./scripts/quality.ps1 format` for mechanical formatting. Keep default tests
offline. Paid online tests require `GEOPHYSAGENT_RUN_ONLINE_TESTS=1`, an optional
`GEOPHYSAGENT_ONLINE_PROFILE`, and the environment-only credential named by that
Profile. The legacy MiniMax opt-in variable remains accepted during migration.

Do not commit API keys, local model configuration, `runs/`, `data/local/`, or
`pi/`. New tools must enter through `ToolProviderRegistry`, declare Schema and
replay policy, and execute under the existing Harness. Do not add direct LLM access
to Shell, arbitrary files, professional implementations, or validators.

Dependency changes require a reason, exact version, official source, license,
security review, Windows 3.11-3.14 test result, and updates to both
`requirements-dev.lock` and `THIRD_PARTY_NOTICES.md`.
