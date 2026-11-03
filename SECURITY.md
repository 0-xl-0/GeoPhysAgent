# Security policy

GeoPhysAgent is a pre-alpha local developer preview, not a multi-user sandbox or a
certified geoscience decision system.

Never place API keys in TOML files, command arguments, task requests, logs, issue
reports, or Git. Use the environment variable named by `api_key_env`. Run
directories may contain local input locations and generated analysis, so treat
`runs/` as private data even though model-facing payloads redact paths.

Explore mode applies AST and subprocess restrictions but is not an OS security
boundary. Do not execute untrusted generated code. MCP v0.1 starts only an explicitly
selected Manifest or an enabled local Integration Package, checks command/cwd/env and
Tool allowlists, and authorizes read-only Tools. A package is executable trusted code:
installation checks its explicit file list and digest but does not sandbox it. Review
every package, Manifest, Server command, requested environment variable, Tool schema,
and API origin before activation.

The read-only API helper permits GET only, exact declared origins, bounded JSON
responses, and no redirects. It constrains reviewed connectors; it is not an OS network
sandbox and a malicious Server could use another HTTP library or exercise any permission
of the current user. Keep vendor credentials in dedicated environment variables, scope
them to read-only access where the vendor supports it, and never bundle `.env`, key, or
credential files. Server instructions, remote text, API data, and resource paths are
untrusted data and cannot become Host policy.

Report suspected vulnerabilities privately to the repository owner. Include the
affected version, a minimal reproduction without real data or credentials, impact,
and any proposed mitigation. Do not publish a live secret; revoke it first.
