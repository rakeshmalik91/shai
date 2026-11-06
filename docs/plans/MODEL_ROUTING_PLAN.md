# Model routing — remaining design and implementation plan

## Goal

Make model selection predictable, explainable, project-configurable, and
appropriate to the work being performed. A role identifies the task; project
configuration identifies suitable candidate models; runtime checks
availability and selects among those candidates.

The initial router and the `chat`, `orchestrator`, and `commit_message`
integrations are implemented. See [the completed routing documentation](../MODEL_ROUTING.md).
This document contains the remaining design decisions and implementation.

## 1. Model-selection policy

### 1.1 Assign the task role explicitly

The workflow/call site must choose the role because it knows what operation it
is about to perform. Do not infer a role by asking a model to classify the
user's prompt, and do not relabel a general agent request as a specialist task
just because the user mentions code, tests, or documentation.

Initial role contract:

| Role | Use when the runtime is specifically doing... |
|---|---|
| `chat` | A direct user-facing conversational response |
| `orchestrator` | Agent reasoning, tool selection, and coordination |
| `planner` | Producing or revising a multi-step execution plan |
| `code` | Generating or editing implementation code in a dedicated step |
| `debug` | Diagnosing a concrete error, failure, or unexpected behavior |
| `test` | Designing tests or analyzing test results in a dedicated step |
| `commit_message` | Generating a Git commit message |
| `review` | Reviewing a diff or implementation for defects and risks |
| `documentation` | Writing or updating project documentation |
| `research` | Synthesizing gathered research and evidence |

A task that moves between stages may make multiple model calls, each with its
own role. Keep `orchestrator` as the owner of the agent/tool loop; specialist
roles must correspond to real, separately dispatched requests.

### 1.2 Establish task requirements before choosing a candidate

For each specialist call site, define a small task profile describing only
requirements that materially affect model suitability. Consider:

- Required context window, including prompt plus expected output headroom.
- Tool/function calling, if the request includes tools.
- Required modality (text, image, audio, etc.).
- Structured-output or schema-following requirements.
- Reasoning/coding quality appropriate to the task.
- Latency and cost budget.
- Data-handling/privacy constraints and local-versus-hosted execution.

The project maintainer should choose candidate models based on these
requirements and their known performance. Do not claim the router can infer
quality, cost, privacy, or capabilities from a model identifier or a basic
provider model catalogue.

### 1.3 Candidate selection algorithm

Keep selection deterministic and explainable. The intended order is:

1. Resolve the explicit task role.
2. Load that role's project-specific candidate list, merged over the tracked
   template.
3. Validate each candidate's configuration and provider registration.
4. Once capability metadata is implemented, filter out candidates that do
   not satisfy the call site's hard requirements (for example, tool support
   or minimum context window). Until then, candidate suitability is curated
   manually in the project config.
5. Check provider/model availability using provider discovery or a supported
   lightweight health endpoint. Never send the real task prompt as a probe.
6. Sort eligible candidates by ascending configured priority; preserve stable
   order for equal priorities.
7. Dispatch to the first eligible candidate.
8. If no candidate is eligible, use the normal configured default model.

Priority is an explicit preference among candidates already deemed suitable;
it is not a hidden quality score. Do not introduce a weighted score that
silently trades correctness against latency or cost. If dynamic scoring is
ever needed, make its inputs, hard constraints, and tie-breaking rules
explicit and observable.

### 1.4 Configuration direction

Current version 1 entries contain `provider`, `model`, and `priority`. Keep
that simple contract working. A future schema revision may add optional
candidate metadata, for example:

- `context_window` (declared usable context capacity).
- `capabilities` (such as `tools`, `vision`, or `structured_output`).
- `data_location` or an equivalent deployment/privacy classification.
- Optional operational hints such as `cost_tier` or `latency_tier`.

These values must be documented, validated, and checked against task-profile
requirements before they influence selection. Unknown metadata must not
silently make a candidate eligible. Do not add these fields until the runtime
can enforce them and tests cover the behavior.

## 2. Implement remaining specialist call sites

Wire roles only where the application has a distinct model request:

- `planner` — task decomposition and plan refinement.
- `code` — code generation/edit decisions, where separate calls exist.
- `debug` — diagnosis of tool/test/runtime failures.
- `test` — test design and failure analysis.
- `review` — diff review.
- `documentation` — documentation generation.
- `research` — research synthesis.

Do not split the current agent loop into artificial requests solely to use
more roles. Preserve the current integrations:

- Chat uses `chat`.
- Agent/tool orchestration uses `orchestrator`.
- `/commit` and `/commit --message-only` use `commit_message` for LLM message
generation. The route must come from the project routing config, not legacy
per-feature provider/model fields.

## 3. Runtime failover and failure classification

When a dispatched model request fails for an eligible availability or
transient reason, try the next untried candidate in priority order, at most
once per candidate. Do not fail over on refusals, invalid user input, policy
decisions, or ordinary model responses. Avoid replaying partial tool-call
state or re-executing tools. If all specialists fail, make at most one attempt
with the configured default unless it was already tried.

Classify at least:

- Unknown/unsupported model.
- Missing credentials and authentication/authorization failure.
- Rate limit or quota exhaustion.
- Transient network/timeout failure.
- Provider/service outage.
- Invalid request or semantic response failure (not eligible for failover).

The retry policy must be bounded, observable, and distinct from the existing
per-provider retry policy. Never duplicate a tool execution as a side effect
of model failover.

## 4. Availability, caching, and diagnostics

- Distinguish discovery failure from a model being absent from a successful
  catalogue response.
- Add bounded provider-specific preflight only where a cheap, reliable
  discovery/health endpoint exists; never probe by sending the full prompt.
- Consider configurable cache TTL and failure cooldown, with hard upper
  bounds.
- Define config reload/cache invalidation behavior, including changes to the
  active project's routing file while the process is running.
- Log the role, selected provider/model, priority, and safe fallback reason.
  Never log credentials or full sensitive prompts.

## 5. Verification and acceptance criteria

Add deterministic tests for:

- Each specialist role being selected only at its intended task boundary.
- Hard capability requirements once capability metadata is implemented.
- Priority ordering, stable ties, invalid candidates, and unavailable models.
- Runtime failover eligibility, candidate attempt limits, and default fallback.
- No duplicate tool execution or unsafe replay of partial tool-call state.
- Cache expiry, failure cooldown, and config reload/invalidation.
- Runtime failover for commit-message requests after the shared routed API call
  fails for an eligible transient/availability reason. The initial command path
  now uses `api_request_for_role(..., "commit_message")`; it currently falls
  back to the pattern generator on request failure rather than trying later
  configured candidates at runtime.
- End-to-end command tests for `/commit`, `/commit --message-only`, and
  `/commit-push`, including project route selection, provider/model restoration,
  explicit-message bypass, and legacy provider/model override rejection.
- Pattern-based commit-message fallback when LLM generation is disabled or
  the routed model cannot be used.
- Backward compatibility for projects without a routing configuration.

Acceptance criteria:

- Every role is attached only to a genuine task-specific model request.
- Model suitability requirements are explicit; the router does not pretend
  availability implies quality or capability.
- Selection is deterministic: hard requirements, availability, priority,
  then default fallback.
- Eligible runtime failures fail over in priority order with bounded retries.
- Refusals and invalid requests do not trigger failover.
- Fallback diagnostics explain the selected candidate and reason safely.
- Routing configuration changes have documented cache/reload semantics.
- The full non-interactive test suite passes.
