# Project-specific model routing

## Status

The project-scoped routing layer is implemented. It selects models from
per-role candidate lists using provider registration and explicit priority.
It does not query provider model catalogues during startup or route resolution.
Unavailable inactive candidates are skipped silently at request time. It is
currently integrated with:

- Chat (`chat`).
- Agent orchestration and tool decisions (`orchestrator`).
- Commit-message generation invoked by `/commit`, `/commit --message-only` and `/commit-push`
  (`commit_message`).

The other roles are defined in the routing schema but do not yet have distinct
runtime call sites. See [the remaining implementation plan](plans/MODEL_ROUTING_PLAN.md).

## Configuration locations

- `assets/model_routing.json` is the tracked shared default configuration.
  Its `default.provider` and `default.model` values provide the fallback model;
  its `models` object defines the supported task roles.
- `<project>/.shai/model_routing.json` is the active project's override and
  stores both its selected default provider/model and task-specific routes.
- A project `default` overrides the shared `default`; project role lists
  override only the roles they explicitly define. Other roles retain their
  template values (normally empty candidate lists).
- There is no separate provider/model selection file. Interactive selection
  updates the project's `default` object in this same routing file.
- Provider credentials, API endpoints, and provider-specific settings remain
  in provider configuration; they do not belong in the routing file.

The `.shai/` directory is always rooted in the selected working project, not the Shai installation. The directory name is consistently lowercase `.shai/` across runtime code,
tests, and documentation. This is the same project-local directory used for
access grants, logs, task state, and backups.

## Example

```json
{
  "version": 1,
  "default": {
    "provider": "openrouter",
    "model": "vendor/default-model"
  },
  "models": {
    "orchestrator": [
      {"provider": "ollama", "model": "qwen2.5:14b", "priority": 1},
      {"provider": "openrouter", "model": "vendor/model:free", "priority": 2}
    ],
    "planner": [],
    "code": [],
    "debug": [],
    "test": [],
    "chat": [],
    "commit_message": [
      {"provider": "openrouter", "model": "vendor/fast-model", "priority": 1},
      {"provider": "ollama", "model": "qwen2.5:14b", "priority": 2}
    ],
    "review": [],
    "documentation": [],
    "research": []
  }
}
```

When no role-specific candidate is usable, Shai uses the configured default
provider/model. Lower positive `priority` values are preferred. Candidate
order is stable for equal priorities. Duplicate provider/model pairs are ignored after the first
entry. A candidate is eligible when its provider is registered and its
configuration entry is structurally valid. The model name is trusted until an
actual request is made; this avoids slow catalogue calls.

## How ShAI decides which model to use

Model selection is a deterministic routing decision, not an LLM guessing
which provider/model it should call:

1. **The workflow chooses the role.** A real call site assigns a stable role
   such as `chat`, `orchestrator`, or `commit_message`. Roles describe the work
   being performed, not a model's quality tier. ShAI does not classify the
   user's wording to choose a role implicitly.
2. **The project config expresses model suitability.** The project owner
   chooses candidates for each role. When choosing them, consider the task's
   expected quality, context size, tool/function-calling needs, structured
   output reliability, modality, latency, cost, privacy, and where the model
   runs. These are configuration/design considerations today; the current
   resolver does not benchmark models or infer these capabilities.
3. **The resolver checks configuration only.** It skips malformed entries
   and unregistered providers. It does not call a provider's model-list API,
   so startup and route resolution are independent of catalogue latency.
4. **Priority chooses the request order.** The lowest numeric priority is
   tried first. If a provider explicitly reports that a routed model does not
   exist or is unavailable, ShAI silently tries the next configured candidate.
   Other errors (authentication, rate limits, network failures, and malformed
   requests) are not treated as discontinued-model signals.
5. **The normal active model is the final fallback.** After routed candidates
   are exhausted, ShAI tries the saved provider/model. Errors from this active
   model are surfaced normally; only inactive routed candidates are silently
   skipped.

This means model *suitability* is currently established by the person
maintaining the project configuration; model *availability* is checked by
the runtime. Future work may add explicit capability metadata and requirement
filters before priority is applied. Until that exists, do not assume the
resolver knows whether a model supports tools, vision, a particular context
window, or structured output merely because it appears in discovery.

## Runtime behavior currently delivered

1. Load the tracked template and apply active-project role overrides.
2. Validate candidate structure/provider registration and sort by ascending
   priority. No model-list API calls are made here.
3. Attempt configured candidates in priority order when a real request occurs.
4. If a routed candidate returns an explicit model-not-found or model-
   unavailable response, suppress the warning/error output and try the next
   configured candidate.
5. After routed candidates are exhausted, try the saved active provider/model.
   If that model fails, show its error normally.
6. Authentication, rate-limit, network, and unrelated API errors are not
   silently skipped; they are surfaced without changing models.

This makes discontinued inactive routes harmless without introducing startup
catalogue requests or silently hiding failures from the active model.

## Supported roles and integrations

| Role | Intended use | Current integration |
|---|---|---|
| `orchestrator` | Agent loop and tool decisions | Agent requests |
| `planner` | Planning and decomposition | Not yet wired |
| `code` | Code generation and edits | Not yet wired |
| `debug` | Debugging and diagnosis | Not yet wired |
| `test` | Test design and failure analysis | Not yet wired |
| `chat` | User-facing conversation | Chat requests |
| `commit_message` | Commit subject/body generation | `/commit` and `/commit --message-only` |
| `review` | Code review | Not yet wired |
| `documentation` | Documentation work | Not yet wired |
| `research` | Research and synthesis | Not yet wired |

### Commit-message routing

The command-driven commit flow uses the shared task-role API boundary:

- `/commit` generates a message through `api_request_for_role(..., "commit_message")`
  before asking for commit confirmation.
- `/commit --message-only` uses the same routed request and returns the generated
  message without creating a commit.
- `/commit-push` inherits the same behaviour because it delegates to `git_commit`.
- The shared router resolves the active project's `.shai/model_routing.json`,
  applies priority/availability selection, temporarily switches the provider and
  model for the request, and restores the normal provider/model afterward.
- The shared provider adapter handles provider-specific request/response formats;
  task-specific system instructions are preserved for Anthropic requests too.

This means command-based commit generation does not maintain a separate HTTP
request implementation or a second model-selection path. A supplied explicit
commit message bypasses generation and therefore does not invoke routing.

The legacy `commit_message.provider` and `commit_message.model` settings in
the general ShAI configuration are no longer used to select the model. The
legacy `enabled`, `prompt`, and pattern settings remain relevant: disabling
LLM generation or encountering a routing/provider/request failure falls back
to the existing pattern-based message generator. Supplying an explicit
message to `/commit` bypasses generated-message selection because no model
request is needed.

## Known limitations and pending work

- Failover is limited to provider errors that clearly identify an unavailable
  model. Provider-specific error formats may require additional recognition.
- Catalogue-based model selection is intentionally reserved for explicit
  interactive model selection (`/model`); it is not part of startup or routing.
- Capability-aware filtering (tool support, context window, modalities,
  structured output, and task-specific constraints) is not implemented.
- Planner, code, debug, test, review, documentation, and research roles need
  genuine task-specific call sites.
- Improve diagnostics to distinguish unsupported models, authentication
  errors, rate limits, transient network failures, and provider outages.
- Define and test cache invalidation/reload behavior when routing config
  changes during a running process.

## Verification

Run:

```sh
python3 -m unittest test.test_model_routing -v
```

The routing tests cover template loading, project overrides, candidate
validation/priority, no-catalogue resolution, silent failover for unavailable
inactive candidates, active-model error propagation, and default fallback.
Commit-message routing has a separate helper-level test. Capability-aware
selection is not implemented.
