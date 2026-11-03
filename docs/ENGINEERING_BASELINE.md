# Engineering baseline

## Supported environment

GeoPhysAgent v0.1 supports 64-bit Windows with CPython 3.11, 3.12, 3.13, and 3.14.
The package remains Python-only. Linux/macOS may work but are not release gates.

The runtime uses only the Python standard library. `requirements-dev.lock` pins the
complete development/build set used by CI. A dependency update must be reviewed as
one change: inspect official release notes and source, license and security impact;
update the lock and third-party notice; run all four CI versions; build artifacts;
then run explicit real-model acceptance with the candidate default Profile before release.

## Clean Windows setup

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --disable-pip-version-check -r requirements-dev.lock
python -m pip install --no-deps --no-build-isolation -e .
geophysagent --help
.\scripts\quality.ps1
```

The same `scripts/quality.ps1` command runs formatting verification, static lint,
and the full offline unit suite locally and in CI. Use
`scripts/quality.ps1 format` to apply formatting and safe lint fixes.

## Online release gate

Paid tests are never part of an ordinary local commit or untrusted pull request.
Run them only after reviewing the requests and accepting the selected provider's quota,
cost, retention and data-transfer terms:

```powershell
$env:GEOPHYSAGENT_RUN_ONLINE_TESTS = "1"
$env:GEOPHYSAGENT_ONLINE_PROFILE = "opencode-go-minimax-m3"
$env:OPENCODE_API_KEY = "..."
python -m unittest tests.test_online_minimax -v
```

`GEOPHYSAGENT_RUN_MINIMAX_TESTS=1` remains a legacy alias for the opt-in switch. The
profile itself is loaded from `GEOPHYSAGENT_ONLINE_CONFIG` (default
`geophysagent.toml`); credentials always come from its `api_key_env`.

The manual CI workflow has the same opt-in semantics. It stores only provider,
model, scenario, TaskRun ID/status, tool/evidence IDs and public usage fields under
the ignored `runs/` directory.

## Reproducible candidate package

Run `scripts/build-release.ps1` from a clean commit. It fixes Python hash seed and
the package timestamp, then builds sdist and wheel without build isolation using the
locked setuptools/build environment. Record the commit ID and SHA-256 of both files
in the release notes. A release candidate requires offline CI plus the manual
selected real-model gate; no automatic publishing is configured.

The public source distribution is licensed under `GPL-3.0-only`, selected by the
project owner on 2026-11-02. See the complete license in `LICENSE` and preserve
third-party notices when distributing changes.
