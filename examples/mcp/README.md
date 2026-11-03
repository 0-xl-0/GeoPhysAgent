# MCP stdio examples

`readonly_server.py` is the v0.1 runnable reference Server. It uses only the Python standard
library, writes only MCP JSON-RPC messages to stdout, supports paged `tools/list`, and exposes
one explicitly allowlisted read-only Tool through `readonly.integration.json`.

Use it with the general Agent:

```powershell
python -m geophysagent run tests/fixtures/basic.las `
  --request "先识别资产，再让外部只读集成复核资产描述" `
  --mcp-manifest examples/mcp/readonly.integration.json `
  --output runs/mcp-readonly
```

`personal_project_server.py` shows that a personal Python function can be wrapped without any
change to GeoDomainHarness. `rest_api_wrapper.py` documents the sidecar boundary for a future
approved REST integration; network permissions remain rejected in v0.1.

Server instructions, Resources, Prompts, Sampling, Elicitation and remote transports are never
promoted into GeoPhysAgent's system prompt by this increment.
