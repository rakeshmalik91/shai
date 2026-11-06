# Project-specific model routing

## Status

Implemented as an initial, availability-aware routing layer. The resolver is
used by Chat (`chat`) and Agent (`orchestrator`). Other roles are defined in
the schema but are not yet wired to separate task-specific model calls.

## Configuration

- `assets/model_routing.json` is the tracked empty template.
- `.Shai/model_routing.json` is the active project's override.
- The project file overrides only roles it explicitly defines.
- The configured default provider/model remains the fallback.
- Provider credentials and endpoint settings remain in the provider
  configuration; they do not belong in the routing file.

The `.Shai` spelling is intentional in the current implementation and is
case-sensitive on case-sensitive filesystems. Do not confuse it with the
lowercase `.shai/` directory used for project-local grants, logs, and backups.

Example:

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
    "commit_message": [],
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

## Runtime behavior currently delivered

1. Load the tracked template and apply active-project role overrides.
2. Validate candidates and sort by ascending priority.
3. Discover available models through the provider model catalogue.
4. Cache discovery results briefly (30 seconds) per project/provider.
5. Select the first configured candidate whose model is discovered.
6. Fall back to the normal configured default when the role is unknown,
   unconfigured, malformed, or has no currently discoverable candidate.
7. Log selected routes and fallback reasons without logging credentials.

The resolver does not send the user's full prompt as an availability probe.
A discovery exception makes that provider unavailable for the short cache
period; the default model remains usable.

## Supported roles

| Role | Intended use | Current integration |
|---|---|---|
| `orchestrator` | Agent loop and tool decisions | Agent |
| `planner` | Planning and decomposition | Not yet wired |
| `code` | Code generation and edits | Not yet wired |
| `debug` | Debugging and diagnosis | Not yet wired |
| `test` | Test design and failure analysis | Not yet wired |
| `chat` | User-facing conversation | Chat |
| `commit_message` | Commit message generation | Not yet wired |
| `review` | Code review | Not yet wired |
| `documentation` | Documentation work | Not yet wired |
| `research` | Research and synthesis | Not yet wired |

Roles are explicit; Shai does not infer them from prompt text.

## Limitations and pending work

- Runtime failover after a selected model request fails is not implemented.
  Current fallback happens before dispatch, based on model discovery.
- Availability caching has a fixed short TTL; configurable cooldowns and
  provider-specific health/preflight checks are not implemented.
- Specialist roles need task-specific call sites before they have an effect.
- Add integration tests for every role as call sites are introduced.
- Improve diagnostics to distinguish unsupported models, authentication
  errors, rate limits, transient network failures, and provider outages.
- Define and test cache invalidation/reload behavior when routing config
  changes during a running process.

## Verification

Run:

```sh
python3 -m unittest test.test_model_routing -v
```

The current tests cover template loading, project overrides, candidate
validation/priority, discovery-based selection, default fallback, and cache
behavior. They do not establish runtime request failover.
