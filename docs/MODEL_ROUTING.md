# Project-specific model routing

## Status

The initial project-scoped routing layer is implemented. It selects models
from per-role candidate lists using provider registration, provider model
discovery, and explicit priority. It is currently integrated with:

- Chat (`chat`).
- Agent orchestration and tool decisions (`orchestrator`).
- Commit-message generation invoked by `/commit`, `/commit --message-only` and `/commit-push`
  (`commit_message`).

The other roles are defined in the routing schema but do not yet have distinct
runtime call sites. See [the remaining implementation plan](plans/MODEL_ROUTING_PLAN.md).

## Configuration locations

- `assets/model_routing.json` is the tracked empty template and defines the
  supported roles and default configuration shape.
- `<project>/.Shai/model_routing.json` is the active project's override.
- The project file overrides only roles it explicitly defines. Other roles
  retain their template values (currently empty candidate lists).
- The provider/model selected through the normal ShAI configuration remains
  the fallback when no configured candidate is usable.
- Provider credentials, API endpoints, and provider-specific settings remain
  in provider configuration; they do not belong in the routing file.

The `.Shai` spelling is intentional in the current implementation and is
case-sensitive on case-sensitive filesystems. Do not confuse it with the
lowercase `.shai/` directory used for project-local grants, logs, and backups.

## Example

```json
{
  "version": 1,
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

Lower positive `priority` values are preferred. Candidate order is stable for
equal priorities. Duplicate provider/model pairs are ignored after the first
entry. A candidate is eligible only when its provider is registered and the
model appears in that provider's discovered model list.

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
3. **The resolver checks eligibility.** It skips malformed entries and
   unregistered providers, then checks the provider's discovered model list.
   Discovery is a lightweight availability check; it does not send the task
   prompt to a model.
4. **Priority chooses among eligible candidates.** The lowest numeric
   priority wins. Use priority to express the preferred order after deciding
   that each candidate is suitable for that role. Do not treat priority as an
   automatic quality score.
5. **The normal default is the final fallback.** If the role has no valid
   configured candidate, or none is discoverable, ShAI uses the provider/model
   selected by the normal project configuration.

This means model *suitability* is currently established by the person
maintaining the project configuration; model *availability* is checked by
the runtime. Future work may add explicit capability metadata and requirement
filters before priority is applied. Until that exists, do not assume the
resolver knows whether a model supports tools, vision, a particular context
window, or structured output merely because it appears in discovery.

## Runtime behavior currently delivered

1. Load the tracked template and apply active-project role overrides.
2. Validate candidates and sort by ascending priority.
3. Discover available models through the provider model catalogue.
4. Cache discovery results briefly (30 seconds) per project/provider.
5. Select the first configured candidate whose provider is registered and
   whose model is discoverable.
6. Fall back to the normal configured default when the role is unknown,
   unconfigured, malformed, or has no currently discoverable candidate.
7. Log selected routes and fallback reasons without logging credentials.

The resolver does not send the user's full prompt as an availability probe.
A discovery exception makes that provider unavailable for a short cache
period; the default model remains usable. Runtime failover after a dispatched
request fails is not implemented yet.

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
- The shared router resolves the active project's `.Shai/model_routing.json`,
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

- Runtime failover after a selected model request fails is not implemented.
  The current fallback happens before dispatch, based on discovery.
- Availability caching has a fixed short TTL; configurable cooldowns and
  provider-specific health/preflight checks are not implemented.
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
validation/priority, discovery-based selection, default fallback, and cache
behavior. Commit-message routing has a separate helper-level test. These tests
do not establish runtime request failover or capability-aware selection.
