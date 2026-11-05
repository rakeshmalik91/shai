# Modular Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md#modular-package-layout) for the detailed
module map, entry-point compatibility, command organization, tool boundaries,
and configuration-preservation guarantees.

The refactor keeps `agent.py` and `chat.py` as stable launch/import entry points
while moving implementation into `modules/engine/` and `modules/ui/`.


## Engine decomposition

`modules/engine/agent_runtime.py` is now the compatibility/runtime composition
surface rather than the home of domain implementations. Domain functions are
extracted into focused modules:

| Module | Responsibility |
|---|---|
| `configuration.py` | Config loading, safeguards, approvals and limits |
| `project_inspection.py` | Filesystem navigation, source inspection and verification |
| `task_plan.py` | Task-plan persistence and continuation |
| `git_operations.py` | Git operations and commit-message generation |
| `file_editing.py` | Approval, validation, atomic edits and check execution |
| `provider_api.py` | Provider request construction, retries and history sanitation |
| `tool_execution.py` | Tool normalization, dispatch, safeguards and execution |
| `tool_presentation.py` | Tool and assistant output formatting |
| `history_recovery.py` | Tool-history repair and recovery notes |
| `model_orchestration.py` | Model request loop and continuation orchestration |

The extracted modules install their functions into the runtime's shared namespace.
This is an intentional compatibility seam: existing callers, tests and extensions
that patch `agent` globals or replace runtime configuration continue to see one
authoritative state rather than divergent copies. The runtime module remains the
stable import surface; new code should import domain modules for ownership and
documentation, while invoking runtime entry points where shared state is needed.

The added `test_engine_module_boundaries.py` suite checks module loading,
runtime exports, compatibility identity and that extracted function bodies are
not duplicated in the runtime file.
