# Known limitations — v0.1 developer preview

- `run` requires a configured LLM and never silently falls back to a deterministic
  product path. Model Profiles support Anthropic Messages-compatible HTTP and
  OpenAI Chat Completions-compatible HTTP. OpenAI Responses, provider-specific OAuth,
  cloud identity chains and custom signing are not implemented. Exact output, quota
  consumption and cost vary by provider/model.
- The candidate plan is bounded and approved, but v0.1 has at most one local asset and no
  user-facing interactive plan editor. Manual resume continues a persisted model
  checkpoint after an external failure; it does not automatically replay a model
  call or generated Python Tool.
- LAS support is a reviewed LAS 2.0 reference subset with wrapped/unwrapped input,
  deterministic rule candidates and a track plot; it is not a complete LAS implementation
  or petrophysical interpretation system. The bundled ten-case corpus is synthetic; authorized
  field-data goldens and professional review remain release gates.
- SEG-Y support is a read-only vertical slice for headers, bounded statistics and
  basic trace QC, not seismic processing, visualization or interpretation.
- Table preprocessing has six explicit operations. Explore can fill capability
  gaps but is exploratory evidence and is not an OS security boundary.
- Synchronous local Tools observe cancellation only at Harness boundaries. Python
  Explore has a subprocess timeout; deterministic LAS/SEG-Y calls are not forcibly
  terminated mid-call.
- MCP support is limited to explicitly configured local stdio Tools using protocol
  `2025-11-25`. Local directory Integration Packages can be installed and enabled, but
  there is no online catalog, signature verification, archive/MCPB installer, dependency
  installer, or management GUI. Packages and Servers are trusted executable code.
- External API support is GET-only JSON through exact declared origins for reviewed
  connectors. The helper is not an OS network sandbox, and GET is only semantically
  read-only when the vendor API defines it that way. There is no remote MCP transport,
  Resources, Prompts, Sampling, write/export/unrestricted-network/external-control Tool,
  or OAuth flow.
- There is still no GUI, remote GeoPhysAgent service, multi-agent runtime, long-term
  memory, or arbitrary Shell/file Tool.
- Only Windows CPython 3.11-3.14 is a release gate. Large, damaged and vendor-specific
  real-world datasets still need a broader authorized golden corpus.
