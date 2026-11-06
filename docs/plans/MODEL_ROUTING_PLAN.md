# Model routing — remaining implementation plan

## Current implementation

The initial routing layer is implemented and documented in
[`../MODEL_ROUTING.md`](../MODEL_ROUTING.md). It resolves candidates by
priority and provider model discovery, caches discovery briefly, and falls
back to the configured default before request dispatch. Chat uses `chat`;
Agent uses `orchestrator`.

This plan contains only work that remains.

## Pending work

### 1. Wire specialist roles to real task boundaries

Add explicit role-aware calls where the runtime makes distinct model requests:

- `planner` — task decomposition and plan refinement.
- `code` — code generation/edit decisions, where separate calls exist.
- `debug` — diagnosis of tool/test/runtime failures.
- `test` — test design and failure analysis.
- `commit_message` — commit subject/body generation.
- `review` — diff review.
- `documentation` — documentation generation.
- `research` — research synthesis.

Do not add role labels to a single shared request merely for appearance.
Keep `orchestrator` responsible for the main agent/tool loop. Do not infer
specialist roles from user text; call sites must select roles intentionally.

### 2. Add bounded runtime failover

When a selected model request fails for an eligible availability/transient
reason, try the next untried candidate in priority order, at most once per
candidate. Do not fail over on refusals, invalid user input, policy decisions,
or ordinary model responses. Avoid replaying partial tool-call state. If all
specialists fail, make at most one attempt with the configured default unless
it was already tried.

### 3. Improve availability diagnostics and controls

- Distinguish unsupported model, missing credentials, authentication,
  rate-limit, transient network, and service-outage outcomes.
- Add bounded provider-specific preflight only where a cheap reliable
  discovery/health endpoint exists; never probe by sending the full prompt.
- Consider configurable cache TTL and failure cooldown, with hard upper
  bounds.
- Define config reload/cache invalidation behavior.
- Keep secrets and sensitive prompts out of logs.

### 4. Verification

Add deterministic tests for each specialist integration, failover eligibility,
candidate attempt limits, no duplicate tool execution, default fallback, and
provider-error propagation. Preserve backward compatibility for projects
without routing configuration.

## Acceptance criteria

- Every specialist role is connected only to a genuine task-specific model
  request.
- Eligible runtime failures fail over in priority order with bounded retries.
- Refusals and invalid requests do not trigger failover.
- Fallback diagnostics explain the selected candidate and reason safely.
- Routing configuration changes have documented cache/reload semantics.
- The full non-interactive test suite passes.
