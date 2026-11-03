# Third-party notices

GeoPhysAgent v0.1 has no third-party runtime Python dependency. It uses Python's
standard library at runtime.

The official Model Context Protocol Python SDK was reviewed for Increment 7B but is
not installed, imported, copied, or redistributed. The minimal stdio client follows
the MCP 2025-11-25 wire specification directly; see ADR-014. The reviewed SDK is MIT
licensed.

The locked development and packaging environment is recorded in
`requirements-dev.lock`:

| Package | Version | Purpose | Source | License |
|---|---:|---|---|---|
| setuptools | 84.0.0 | PEP 517 build backend | PyPI / PyPA | MIT |
| build | 1.6.0 | sdist and wheel frontend | PyPI / PyPA | MIT |
| packaging | 26.3 | transitive packaging utilities | PyPI / PyPA | Apache-2.0 OR BSD-2-Clause |
| pyproject-hooks | 1.2.0 | transitive PEP 517 hooks | PyPI / PyPA | MIT |
| colorama | 0.4.6 | build's Windows terminal dependency | PyPI | BSD-3-Clause |
| ruff | 0.16.6 | formatter and static linter | PyPI / Astral | MIT |

Versions and licenses were checked against official PyPI project metadata on
2026-09-07. Development tools are not bundled in the GeoPhysAgent wheel.

The local `pi/` directory is reference-only material cloned separately by the
project owner. GeoPhysAgent does not copy, import, package, test, or redistribute
it; see `docs/REFERENCE_POLICY.md`.
