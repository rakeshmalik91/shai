# Shai implementation plans

Only work that remains future-facing belongs in this directory. Implemented
feature specifications live in `docs/`:

- [`../SHAI_COMMAND.md`](../SHAI_COMMAND.md) — implemented `shai_command`
  natural-language routing.
- [`../OS_COMMAND.md`](../OS_COMMAND.md) — implemented restricted `os_command`
  execution.

## Remaining plans

- [`EXECUTION_FLOW_ARCHITECTURE.md`](EXECUTION_FLOW_ARCHITECTURE.md) — future
  refactor of session lifecycle, state machine, orchestration, event flow, and
  terminal presentation. The command features are already implemented; the
  broader runtime refactor remains future work.
- [`PROJECT_PATH_ACCESS_CONTROL.md`](PROJECT_PATH_ACCESS_CONTROL.md) — further
  hardening of project-root and filesystem access controls.
- [`TOOLS.md`](TOOLS.md) — future tool roadmap and design notes.
- [`OS_COMMAND_SANDBOX.md`](OS_COMMAND_SANDBOX.md) — future OS-level sandbox and network isolation for untrusted project code.
- [`LLM_CLARIFICATION_QUESTIONS.md`](LLM_CLARIFICATION_QUESTIONS.md) — future
  clarification-question behavior.
