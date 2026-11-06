# Plan: Project-Specific Multi-Model Routing

**Status:** Proposed  
**Scope:** ShAI agent runtime  
**Configuration location:** `.Shai/model_routing.json`

## 1. Objective

Allow ShAI to use different language models for different classes of work while keeping one configured default model as the reliable fallback.

The routing layer should select a model by task purpose, not by whichever component happens to call the provider. It should support separate models for orchestration, planning, implementation, debugging, testing, conversation, commit messages, and other specialist tasks.

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
  "version": 1,
  "models": {
    "orchestrator": {
      "provider": "openai",
      "model": "model-for-orchestration"
    },
    "planner": {
      "provider": "openai",
      "model": "model-for-planning"
    },
    "code": {
      "provider": "openai",
      "model": "model-for-code"
    },
    "debug": {
      "provider": "openai",
      "model": "model-for-debugging"
    },
    "test": {
      "provider": "openai",
      "model": "model-for-test-analysis"
    },
    "chat": {
      "provider": "openai",
      "model": "model-for-conversation"
    },
    "commit_message": {
      "provider": "openai",
      "model": "model-for-commit-messages"
    },
    "review": {
      "provider": "openai",
      "model": "model-for-code-review"
    },
    "documentation": {
      "provider": "openai",
      "model": "model-for-documentation"
    },
    "research": {
      "provider": "openai",
      "model": "model-for-research"
    }
  }
}
```

The model strings above are placeholders, not recommendations or hard-coded supported model names.

### 4.1 Route shape

Each role maps to a route object:

- `provider`: provider identifier already registered in ShAI.
- `model`: model identifier accepted by that provider's existing configuration.

Do not add API keys, base URLs, secrets, temperature, token limits, or arbitrary provider settings to this file. Provider-level configuration remains in the existing provider configuration.

### 4.2 Initial supported roles

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

## 5. Routing behavior

Introduce one shared model-resolution entry point, conceptually:

```python
resolve_model(task_role, project_root, default_model)
```

It should return a normalized model selection and routing metadata. It should not instantiate providers or perform a model request itself.

Suggested resolution sequence:

1. Obtain the already-resolved default model from the existing configuration/provider layer.
2. Normalize and validate the requested task role.
3. Locate the active project root using the same project-root resolution used by the rest of ShAI.
4. Load and validate `.Shai/model_routing.json` (preferably once per configuration fingerprint, with explicit reload when the file changes).
5. Find the route for the requested role.
6. Validate that the provider exists and the model is accepted by the existing provider registry/configuration.
7. Return the selected route if all checks pass.
8. Otherwise return the default model and a structured fallback reason.

Every model invocation should go through this shared resolver, including background tasks and tool-related LLM calls. Avoid separate ad-hoc routing logic in individual modules.

## 6. Fallback requirements

The default configured model is the fallback in all of these cases:

- `.Shai` directory does not exist.
- `model_routing.json` does not exist.
- File cannot be read due to permissions or I/O errors.
- JSON is malformed or empty.
- Root value is not an object.
- `version` is missing or unsupported.
- `models` is missing or is not an object.
- A role entry is missing.
- A role entry is not an object.
- `provider` or `model` is missing, blank, or the wrong type.
- Provider is not registered.
- Model is not recognized or permitted by that provider.
- A route cannot be instantiated or fails a preflight validation.
- The requested role is unknown.

A malformed route should not invalidate otherwise valid routes unless the overall document structure or version is invalid. For example, an invalid `debug` entry should cause only `debug` to use the default; a valid `code` route should remain usable.

If the configured specialist model fails during a request, define a bounded fallback policy: retry the request once using the default model for eligible transient/provider/model errors. Do not loop indefinitely. Do not silently retry policy refusals or invalid user requests as though they were infrastructure failures.

If the default model itself is unavailable, surface the normal provider error. Do not invent a second fallback.

## 7. Integration plan

### Phase 1 — Configuration and resolver

- Add a dedicated model-routing configuration module.
- Define the supported role registry and schema version.
- Resolve the active project root consistently.
- Load `.Shai/model_routing.json`.
- Validate the document and individual route entries.
- Implement default-model fallback for every invalid/missing case.
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
- Explain all invalid-config fallback behavior.
- Document project isolation and the case-sensitive `.Shai` directory.
- Do not migrate or copy global settings into project files automatically.
- Ensure generated project-local configuration is treated as local state and is not committed by default; document how a team may deliberately version-control it if desired.

## 8. Testing plan

Tests must be deterministic and must never prompt for interactive input. Use temporary project roots, temporary `.Shai` directories, mocked provider registries, and fake model clients.

### Configuration tests

- Missing `.Shai` directory uses default.
- Missing file uses default.
- Empty file and malformed JSON use default.
- Invalid JSON root, version, or `models` type uses default.
- Unknown version uses default.
- Valid configuration loads all supported roles.
- Missing role uses default.
- Unknown role uses default.
- Invalid route shape falls back for that role only.
- Unknown provider and unsupported model fall back for that role only.
- Blank and non-string provider/model values are rejected.
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
- Each specialist role selects its configured model.
- Invalid specialist selection uses the default.
- Specialist runtime failure performs at most one eligible default retry.
- Default-model failure surfaces normally.
- Fallback diagnostics identify the reason without leaking secrets.

### Regression tests

- Existing provider configuration continues to work unchanged.
- Existing single-model projects behave exactly as before.
- Tool execution, command permissions, project path controls, and interactive access approval are unaffected.
- Test suite does not read from stdin or wait for user input.

## 9. Acceptance criteria

The feature is ready when:

1. Routing configuration exists only at `<project-root>/.Shai/model_routing.json`.
2. The global/default model remains defined by the existing ShAI configuration.
3. A missing or invalid routing file never prevents startup or ordinary model calls.
4. Every absent/invalid route resolves to the default configured model.
5. A valid route for one role does not get discarded because a different role entry is invalid.
6. All listed task categories use the central resolver.
7. Projects cannot inherit each other's routing configuration.
8. Routing decisions and fallback reasons are observable without exposing secrets.
9. Tests cover configuration, isolation, routing, runtime fallback, and backward compatibility.
10. All tests run non-interactively.

## 10. Decisions to preserve during implementation

- Use `.Shai` exactly as specified; do not silently substitute `.shai`.
- Keep generic/global provider and default-model configuration separate from project-specific routing.
- Do not make the project routing file a second source of provider credentials or provider definitions.
- Do not silently choose a random model if a route is invalid.
- Do not let configuration parsing errors escape into the agent loop.
- Keep role names stable and centrally defined so logs, tests, and configuration use the same identifiers.
