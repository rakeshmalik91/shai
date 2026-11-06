# Plan: Project-Specific Multi-Model Routing

**Status:** Implemented (initial routing integration)  
**Scope:** ShAI agent runtime  
**Configuration location:** `.Shai/model_routing.json`

## 1. Objective

Allow ShAI to use different language models for different classes of work while keeping one configured default model as the reliable fallback.

The routing layer should select a model by task purpose, not by whichever component happens to call the provider. Each task category can define multiple candidate models, ordered by priority. ShAI selects the highest-priority candidate that is currently available and falls through to the next candidate when a candidate is unavailable.

The configuration is project-specific. A project's routing preferences must not silently affect another project.

## 2. Design principles

1. **Default-first reliability:** the default model already configured by ShAI remains authoritative as the fallback.
2. **Fail closed to the default:** a missing, malformed, unsupported, or invalid routing configuration must never prevent the agent from running. ShAI falls back to the default configured model.
3. **Project isolation:** read routing configuration only from the active project's `.Shai` directory.
4. **Explicit task identity:** callers identify the task role; the routing layer resolves the model.
5. **Provider-neutral routing:** a route identifies a model using the same provider/model representation already understood by ShAI's provider configuration.
6. **No hidden model substitution:** log the requested role, selected model, and fallback reason without exposing secrets.
7. **Backward compatibility:** components that do not supply a role continue using the default configured model.
8. **Security:** project routing configuration may select only providers and models available to the current ShAI installation. It must not contain credentials or override provider security settings.

## 3. Configuration location and precedence

Use this project-local file:

```text
<project-root>/
└── .Shai/
    └── model_routing.json
```

The `.Shai` directory name is intentional and case-sensitive on filesystems that distinguish case.

The global/default model remains configured through ShAI's existing core configuration. Do not duplicate or redefine that default in the project routing file.

Resolution precedence:

1. Load the default model from the existing ShAI configuration.
2. Look for `.Shai/model_routing.json` under the active project root.
3. If the file is absent, invalid, unreadable, or contains an invalid route, use the default model.
4. If the file is valid but a requested role is not configured, use the default model.
5. If the selected route's provider/model cannot be resolved or is unavailable at runtime, fall back to the default model when possible.

Do not search parent directories, the user's home directory, or another project's `.Shai` directory for routing configuration. This avoids accidental cross-project inheritance.

## 4. Proposed configuration schema

Example:

```json
{
  "version": 2,
  "availability": {
    "cache_seconds": 30,
    "probe_timeout_seconds": 5,
    "failure_cooldown_seconds": 60
  },
  "models": {
    "orchestrator": [
      {"provider": "openai", "model": "preferred-orchestrator-model", "priority": 1},
      {"provider": "anthropic", "model": "secondary-orchestrator-model", "priority": 2}
    ],
    "planner": [
      {"provider": "openai", "model": "preferred-planning-model", "priority": 1},
      {"provider": "kilo-free", "model": "fallback-planning-model", "priority": 2}
    ],
    "code": [
      {"provider": "openai", "model": "preferred-code-model", "priority": 1},
      {"provider": "anthropic", "model": "fallback-code-model", "priority": 2}
    ],
    "debug": [
      {"provider": "openai", "model": "preferred-debug-model", "priority": 1},
      {"provider": "kilo-free", "model": "fallback-debug-model", "priority": 2}
    ],
    "test": [
      {"provider": "openai", "model": "preferred-test-model", "priority": 1},
      {"provider": "anthropic", "model": "fallback-test-model", "priority": 2}
    ],
    "chat": [
      {"provider": "openai", "model": "preferred-chat-model", "priority": 1}
    ],
    "commit_message": [
      {"provider": "openai", "model": "preferred-commit-model", "priority": 1}
    ],
    "review": [
      {"provider": "anthropic", "model": "preferred-review-model", "priority": 1}
    ],
    "documentation": [
      {"provider": "openai", "model": "preferred-documentation-model", "priority": 1}
    ],
    "research": [
      {"provider": "openai", "model": "preferred-research-model", "priority": 1}
    ]
  }
}
```

All model identifiers are illustrative placeholders. Replace them with identifiers supported by the installed provider configuration.

### 4.1 Candidate shape and priority

Each role maps to an array of candidate objects:

- `provider`: provider identifier already registered in ShAI.
- `model`: model identifier accepted by that provider's existing configuration.
- `priority`: positive integer; **1 is highest priority**. Lower numbers are tried first.

Candidates are sorted by ascending `priority`; original array order is the stable tie-breaker. Duplicate priorities are allowed but should produce a validation warning. Duplicate provider/model pairs within one role should be ignored after the first occurrence.

Do not add API keys, base URLs, secrets, temperature, token limits, or arbitrary provider settings to this file. Provider-level configuration remains in the existing provider configuration.

### 4.2 Supported task roles

| Role | Intended responsibility |
|---|---|
| `orchestrator` | Agent-loop control, tool selection, delegation, and deciding when a task is complete |
| `planner` | Breaking requests into steps, dependencies, milestones, and execution plans |
| `code` | Writing and modifying application code |
| `debug` | Diagnosing errors, tracing failures, and proposing fixes |
| `test` | Designing tests, analyzing test failures, and assessing coverage |
| `chat` | General user-facing conversation and clarification |
| `commit_message` | Producing concise commit subjects and bodies from actual changes |
| `review` | Reviewing diffs for correctness, regressions, and security issues |
| `documentation` | Writing and updating technical documentation |
| `research` | Synthesizing retrieved information and technical research |

Roles should be constants or a centrally maintained registry in code. Unknown role names must not be treated as arbitrary provider/model selectors.

## 5. Availability-aware routing

Introduce one shared model-resolution entry point, conceptually:

```python
resolve_model(task_role, project_root, default_model)
```

It returns the selected model and routing metadata. It does not instantiate providers or perform the task request itself.

### 5.1 Candidate selection

1. Load the active project's routing file.
2. Validate the role's candidate list and each candidate independently.
3. Sort valid candidates by ascending `priority` (stable array order for ties).
4. Check candidates in that order using the provider adapter's availability mechanism.
5. Select the first candidate reported available.
6. If a candidate is unavailable, record the reason and continue to the next candidate.
7. If no specialist candidate is available, select the default model from existing ShAI configuration.

A missing role or empty candidate list goes directly to the default model.

### 5.2 What "available" means

Availability must be based on evidence, not assumptions:

- Provider is registered and configured.
- Model identifier is accepted by that provider.
- Required credentials/configuration are present.
- Provider/model is not in an active cooldown after a recent transient failure.
- If the provider offers a cheap model-list or health endpoint, use it.
- Otherwise use a bounded, low-cost provider-specific preflight only when supported.

Do not send the full user prompt merely to probe availability. Do not treat an HTTP timeout as proof that a model does not exist. Distinguish unsupported-model/configuration errors, authentication errors, rate limits, transient network failures, and service outages.

Availability probes are cached for `cache_seconds` to avoid repeated requests. A failed candidate may be placed in cooldown for `failure_cooldown_seconds`; expiry allows it to be checked again. `probe_timeout_seconds` bounds each probe. These settings are project routing preferences, subject to safe global hard limits.

### 5.3 Runtime failover

If the selected candidate fails during the actual request:

- For eligible provider/availability failures (rate limiting, transient network errors, service unavailable, model unavailable), mark it temporarily unavailable and try the next untried candidate in priority order.
- Retry each candidate at most once for the same request.
- Do not fail over on policy refusals, invalid user input, content validation failures, or ordinary model answers.
- If all candidates fail, make one bounded attempt with the default configured model unless it was already tried.
- If the default model also fails, surface the normal provider error.
- Preserve the original conversation/task input when switching candidates. Do not append partial or duplicate tool-call state from a failed attempt without validating it.

### 5.4 Default fallback

The default configured model remains the final fallback when:

- Project `.Shai` directory or routing file is missing.
- Routing file cannot be read, is malformed, or has an unsupported schema version.
- `models` is missing or is not an object.
- Requested role is missing or unknown.
- Role candidate list is empty or structurally invalid.
- Every candidate is invalid, unsupported, unconfigured, or unavailable.
- Every candidate fails with an eligible runtime failure.

A malformed candidate should be skipped without discarding other valid candidates in that role. A malformed role list should fall back for that role only. An invalid top-level schema/version should disable routing and use the default model for all roles.

If the default model itself is unavailable, surface the normal provider error. Do not invent a second fallback.

## 6. Integration plan

### Phase 1 — Configuration and resolver

- Add a dedicated model-routing configuration module.
- Define the supported role registry and schema version.
- Resolve the active project root consistently.
- Load `.Shai/model_routing.json`.
- Validate the document, role candidate lists, candidate objects, and priorities independently.
- Implement priority-ordered availability checks, bounded runtime failover, and default-model fallback for every missing/invalid/exhausted case.
- Keep the existing default model configuration unchanged.
- Add safe diagnostics for invalid configuration.

### Phase 2 — Runtime integration

Replace direct model selection at call sites with the shared resolver.

Integrate roles into:

- Main agent/orchestration loop.
- Planning and task decomposition.
- Code generation and code-edit workflows.
- Debugging and error diagnosis.
- Test generation, test execution analysis, and failure triage.
- User-facing chat.
- Commit subject/body generation.
- Code review.
- Documentation generation.
- Research/synthesis workflows.

Where a component performs multiple distinct tasks, pass the appropriate role for each model call rather than assigning one model to the entire component.

### Phase 3 — Observability

For each model request, record:

- Requested task role.
- Selected provider and model.
- Whether the default or a specialist route was selected.
- Fallback reason/code, if applicable.
- Whether a runtime retry used the default model.

Never log API keys, authorization headers, provider secrets, or full sensitive prompts merely to explain routing.

Expose enough diagnostic information to answer: "Which model handled this task, and why?"

### Phase 4 — Documentation and migration

- Document `.Shai/model_routing.json` and all supported roles.
- Provide a sample configuration with placeholder model identifiers.
- Explain that omitted roles use the default.
- Explain candidate priority, availability probes/cache/cooldown, runtime failover, and all invalid-config fallback behavior.
- Document project isolation and the case-sensitive `.Shai` directory.
- Do not migrate or copy global settings into project files automatically.
- Ensure generated project-local configuration is treated as local state and is not committed by default; document how a team may deliberately version-control it if desired.

## 7. Testing plan

Tests must be deterministic and must never prompt for interactive input. Use temporary project roots, temporary `.Shai` directories, mocked provider registries, and fake model clients.

### Configuration tests

- Missing `.Shai` directory uses default.
- Missing file uses default.
- Empty file and malformed JSON use default.
- Invalid JSON root, version, or `models` type uses default.
- Unknown version uses default.
- Valid configuration loads multiple candidates for each supported role.
- Candidates are sorted by ascending priority with stable ties.
- Missing role and empty candidate list use default.
- Unknown role uses default.
- Invalid top-level schema/version uses default for all roles.
- Invalid candidate is skipped while valid candidates for that role remain usable.
- Invalid role candidate-list shape uses default for that role only.
- Unknown provider and unsupported model candidates are skipped.
- Blank and non-string provider/model values and invalid priorities are rejected.
- Unreadable file and I/O failure use default.
- Extra unknown fields are handled according to a documented strictness policy.

### Project isolation tests

- Project A's routing is not used in Project B.
- Parent-directory `.Shai` files are not inherited.
- Home-directory configuration is not inherited.
- Relative and absolute project-root handling resolves to the same active project.
- Symlinked project roots follow the repository's established project-root/security policy.
- A config change is detected according to the cache/reload contract.

### Runtime routing tests

- Every task role reaches the resolver.
- Existing call sites without a role use the default.
- Each specialist role selects the highest-priority available candidate.
- Unavailable candidates are skipped and the next priority candidate is tried.
- Invalid/exhausted specialist candidate lists use the default.
- Specialist runtime failure performs bounded failover through untried candidates and then the default.
- Default-model failure surfaces normally.
- Fallback diagnostics identify the reason without leaking secrets.

### Regression tests

- Existing provider configuration continues to work unchanged.
- Existing single-model projects behave exactly as before.
- Tool execution, command permissions, project path controls, and interactive access approval are unaffected.
- Test suite does not read from stdin or wait for user input.

## 8. Acceptance criteria

The feature is ready when:

1. Routing configuration exists only at `<project-root>/.Shai/model_routing.json`.
2. The global/default model remains defined by the existing ShAI configuration.
3. A missing or invalid routing file never prevents startup or ordinary model calls.
4. A missing role, malformed role list, or exhausted candidate list resolves to the default configured model; an invalid individual candidate is skipped while lower-priority candidates remain eligible.
5. A valid route for one role does not get discarded because a different role entry is invalid.
6. All listed task categories use the central resolver.
7. Each category supports multiple priority-ranked candidates and chooses the highest-priority available candidate.
8. Availability probes are bounded/cached, and runtime failover is limited to eligible failures without retrying refusals or invalid requests.
9. Projects cannot inherit each other's routing configuration.
10. Routing decisions, candidate skips, availability state, and fallback reasons are observable without exposing secrets.
11. Tests cover configuration, isolation, priority ordering, availability caching, runtime failover, and backward compatibility.
12. All tests run non-interactively.

## 9. Decisions to preserve during implementation

- Use `.Shai` exactly as specified; do not silently substitute `.shai`.
- Keep generic/global provider and default-model configuration separate from project-specific routing.
- Do not make the project routing file a second source of provider credentials or provider definitions.
- Do not silently choose a random model if a route is invalid.
- Do not let configuration parsing errors escape into the agent loop.
- Keep role names stable and centrally defined so logs, tests, and configuration use the same identifiers.


## Implementation notes

The initial implementation adds `assets/model_routing.json` as the tracked
empty template and `.Shai/model_routing.json` as the active project's ignored
override. `modules/model_routing.py` validates roles/candidates, sorts by
priority, checks discovered provider models, caches discovery briefly, and
falls back to the configured default. Chat requests are integrated with the
`chat` role; Agent requests are integrated with `orchestrator`.

The remaining specialist roles (`planner`, `code`, `debug`, `test`,
`commit_message`, `review`, `documentation`, and `research`) are declared and
ready for explicit task-specific model calls. They are not automatically
inferred from prompt text. Runtime failover after a request has already been
sent is not implemented in this initial integration; unavailable candidates
are skipped before request dispatch.
