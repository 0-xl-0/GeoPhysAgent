# 🌍 GeoPhysAgent

**Geophysical analysis backed by tools, inspectable evidence, and traceable execution.**

[简体中文](README.md) | **English**

🐍 Python 3.11–3.14 · 🪟 64-bit Windows · 🧪 v0.1 developer preview · 📜 GPL-3.0-only

GeoPhysAgent is an LLM agent for geophysical workflows. Describe your goal in natural language; the model proposes a plan and calls specialized tools. A Harness—the execution control layer—constrains execution, validates results, and records evidence and artifacts.

The project follows **LLM-first · Local-execution · Harness-governed**: the model plans and explains, local or authorized external tools compute, and the Harness controls and traces the process. It is intended for learning agent engineering, researching verifiable analysis workflows, and developing geophysical tool integrations.

> This is a developer preview with well-log QC, table processing, basic SEG-Y inspection, and read-only external integrations. Professional interpretation still requires human review; this is not yet a complete production geophysics platform.

**Quick links:** [Quickstart](#quickstart) · [Natural-language tasks](#agent) · [Examples](#examples) · [Documentation](#docs) · [Contributing](#contributing)

## ✨ Capabilities

| Area | Current support |
|---|---|
| 📈 LAS well logs | LAS 2.0 subset, curve statistics, missing/range/flatline/spike/jump candidates, SVG track plots |
| 📋 Tabular data | TXT / CSV / TSV structure inspection, parameterized preprocessing, restricted Python exploration |
| 🌊 SEG-Y seismic | Read-only text/binary/trace header inspection, streaming amplitude statistics, basic trace QC |
| 🤖 Model-driven tasks | Named model profiles, multi-tool loops, context budgets, bounded retries, manual resume |
| 🔌 External tools | Local MCP stdio, Integration Packages, read-only JSON APIs with exact origin allowlists |
| 🔎 Traceable execution | Structured plans, tool attempts, Evidence, Validators, artifact manifests, checkpoints, integrity checks |

The runtime uses only the Python standard library: **no third-party Python runtime dependencies**. Development, formatting, and packaging tools are installed separately.

<a id="quickstart"></a>
## 🚀 Quickstart: run an offline analysis

Supported environment: **64-bit Windows, CPython 3.11–3.14, PowerShell**. These commands use Python 3.11; adjust the version to match your installation.

```powershell
git clone https://github.com/0-xl-0/GeoPhysAgent.git
cd GeoPhysAgent
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .

python -m geophysagent analyze tests/fixtures/basic.las --output runs/quickstart
python -m geophysagent validate runs/quickstart
```

This workflow **needs no model API key and makes no model calls**. During installation, pip may need internet access to download build tools. Inspect these outputs in `runs/quickstart/`:

| File | Purpose |
|---|---|
| `result.json` | Machine-readable analysis results |
| `report.md` | Report separating observations, rule-based judgments, and capability limits |
| `well-log.svg` | Well-log curve plot |
| `well-log.figure.json` | Figure provenance metadata |
| `run-record.json` | Task, tool, evidence, and validation records |

The sample LAS intentionally contains missing values, so `completed_with_warnings` is an expected QC outcome. `validate` checks run and artifact integrity; it does not certify a professional interpretation.

> 💡 Use a new `--output` directory for each new task. Existing tasks are protected from overwrite. Reactivate `.venv` in a new terminal, or invoke `.\.venv\Scripts\python.exe` directly.

<a id="agent"></a>
## 🤖 Run a natural-language task

Copy the model configuration and provide credentials through the environment variable specified by its `api_key_env`:

```powershell
Copy-Item geophysagent.example.toml geophysagent.toml
$env:OPENCODE_API_KEY = "YOUR_API_KEY"
python -m geophysagent models
python -m geophysagent doctor

python -m geophysagent run tests/fixtures/basic.las `
  --request "Identify the data, check its quality, and explain the main issues" `
  --output runs/agent-las
```

- `models` and `doctor` perform local checks without calling the model. `run` sends task context to the provider and may incur charges.
- The example TOML contains named profiles. Change `[models].default` to select the default, or pass `--profile PROFILE_NAME` for one run.
- Anthropic Messages-compatible and OpenAI Chat Completions-compatible interfaces are supported. Model name, protocol, base URL, and credential variable must match. Example profiles do not guarantee continued provider availability.
- Keep keys in environment variables, outside TOML and Git. `geophysagent.toml` is ignored by version control.

The model submits a candidate plan before tools become available under Harness constraints. The final response is saved as `agent-response.md`; verified facts carry Evidence references, while model explanations are labeled separately. Model failures never silently switch the task to deterministic analysis.

📖 Configuration and profile switching: [Model configuration](docs/MODEL_CONFIGURATION.md) (Chinese).

<a id="examples"></a>
## 🛠️ Common examples

Run these commands from the repository root with the virtual environment activated.

**Identify a file and inspect its table structure:**

```powershell
python -m geophysagent inspect tests/fixtures/well_log.txt
```

**Run a traceable table preprocessing pipeline:**

```powershell
python -m geophysagent preprocess tests/fixtures/well_log.csv `
  --pipeline examples/preprocess-well-log.json `
  --output runs/preprocessed
```

**Inspect SEG-Y without modifying it:** first place a file you are authorized to use at `data/local/example.sgy`. Real SEG-Y data is not bundled.

```powershell
python -m geophysagent segy data/local/example.sgy --output runs/segy-check
```

**Explore a table question beyond the fixed tools (requires a model):**

```powershell
python -m geophysagent explore tests/fixtures/well_log.txt `
  --request "Compute first-difference statistics for each curve and depth, and explain the limitations" `
  --output runs/explore-basic
```

Explore outputs are exploratory evidence. Review the code and results; candidate tools are not automatically activated as production tools. See [Advanced usage](docs/USAGE.md) (Chinese) and [Explore mode](docs/EXPLORE_MODE.md).

## 🔌 Connect external tools

Activate a reviewed local MCP stdio server through an explicit Integration Manifest:

```powershell
python -m geophysagent run tests/fixtures/basic.las `
  --request "Identify the asset and cross-check its description with the external read-only tool" `
  --mcp-manifest examples/mcp/readonly.integration.json `
  --output runs/mcp-readonly
```

Current integrations support read-only tools, local packages, and allowlisted JSON API GET requests. Installed packages can also support external queries without a local file. Real software requires its own adapter and authorization.

📖 [MCP examples](examples/mcp/README.md) · [Local package examples](examples/integrations/README.md) · [Integration and API boundaries](docs/INTEGRATION_PACKAGES.md)

## 🔄 Inspect and resume tasks

```powershell
python -m geophysagent status runs/agent-las
python -m geophysagent validate runs/agent-las
```

After resolving a model or configuration failure, explicitly continue a waiting general Agent task:

```powershell
python -m geophysagent run --resume --output runs/agent-las
```

Use `resume RUN_DIRECTORY` for deterministic tasks and `cancel RUN_DIRECTORY --reason "reason"` to cancel. Only tools satisfying safe-replay conditions are automatically replayed. Supply the same Manifest again when resuming an MCP task.

📖 [Task lifecycle](docs/HARNESS_LIFECYCLE.md) · [Context and recovery](docs/CONTEXT_ASSEMBLY.md)

## 🧪 Development and validation

Install the locked development tools in the activated virtual environment, then run the same checks as CI:

```powershell
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps --no-build-isolation -e .
.\scripts\quality.ps1
```

Default tests use fake models and local test servers without public internet access. Paid model tests require explicit opt-in. CI covers Windows Python 3.11–3.14. Build the source distribution and wheel with:

```powershell
.\scripts\build-release.ps1
```

Evaluate recorded reference tasks without re-executing tools or calling a model:

```powershell
python -m geophysagent analyze tests/fixtures/las_reference/normal.las `
  --output runs/reference/las-normal
python -m geophysagent evaluate --suite examples/reference/las-suite.json `
  --runs-root runs/reference --output runs/reference/las-evaluation.json
```

📖 [Engineering baseline](docs/ENGINEERING_BASELINE.md) · [Reference product and evaluation](docs/REFERENCE_PRODUCT.md) · [GitHub Actions](https://github.com/0-xl-0/GeoPhysAgent/actions)

## ⚠️ Current limitations

- LAS reference fixtures are synthetic. Rule-based findings are not geological or reservoir interpretation conclusions; field-data validation and professional review are still needed.
- SEG-Y support is basic read-only inspection, without seismic processing, imaging, or an interpretation workbench.
- Explore, MCP servers, and API connectors are **not OS-level security sandboxes**. Run only reviewed, trusted code and integrations.
- There is no GUI, remote MCP, multi-agent runtime, long-term memory, or write access to external software yet.
- Linux/macOS are not formal release-validation platforms; Windows is the supported baseline.

📖 [Known limitations](docs/KNOWN_LIMITATIONS.md) · [Security policy](SECURITY.md)

<a id="docs"></a>
## 📚 Documentation

Both READMEs cover the same topics. Detailed design documents are currently mostly in Simplified Chinese, with some engineering documents in English.

| Topic | Documents |
|---|---|
| Detailed commands, Explore, integrations, online acceptance | [Advanced usage](docs/USAGE.md) |
| Implementation progress and next steps | [Implementation status](docs/IMPLEMENTATION_STATUS.md) · [Roadmap](ROADMAP.md) · [TODO](TODO.md) |
| Architecture and Harness | [Architecture](docs/ARCHITECTURE.md) · [Harness Core](docs/HARNESS_CORE.md) |
| Models and context | [Model configuration](docs/MODEL_CONFIGURATION.md) · [Context Assembly](docs/CONTEXT_ASSEMBLY.md) |
| Seismic data coverage | [SEG-Y support](docs/SEGY_SUPPORT.md) |
| External tool contracts | [MCP Provider](docs/MCP_STDIO_PROVIDER.md) · [Integration Packages](docs/INTEGRATION_PACKAGES.md) |

Repository layout: `src/` source code, `tests/` tests and synthetic fixtures, `examples/` examples, `docs/` design and usage documentation, and `scripts/` quality and build commands. `runs/` and `data/local/` are private local directories excluded from version control.

<a id="contributing"></a>
## 🤝 Contributing

Use [Issues](https://github.com/0-xl-0/GeoPhysAgent/issues) to report reproducible problems and suggest improvements, or open a pull request. New tools should include input/output contracts, tests, validation rules, and provenance. Keep credentials, unauthorized data, and run records out of commits.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow. Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## 📜 License

Copyright (c) 2026 GeoPhysAgent contributors.

Licensed under **GNU GPL v3.0 only** (`GPL-3.0-only`); see [LICENSE](LICENSE) for the full terms. Third-party materials retain their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
