# ShAI implementation plans

Only unfinished work belongs in this directory. Implemented feature references
live in `docs/`:

- [`../SHAI_COMMAND.md`](../SHAI_COMMAND.md) — implemented `shai_command`
  natural-language routing.
- [`../OS_COMMAND.md`](../OS_COMMAND.md) — implemented restricted `os_command`
  execution.
- [`../PROJECT_PATH_ACCESS_CONTROL.md`](../PROJECT_PATH_ACCESS_CONTROL.md) —
  implemented project path permissions and interactive grants.
- [`../MODEL_ROUTING.md`](../MODEL_ROUTING.md) — current availability-aware
  model routing implementation.
- [`../TOOLS.md`](../TOOLS.md) — delivered tool inventory and contracts.

## Remaining plans

- [`MODEL_ROUTING_PLAN.md`](MODEL_ROUTING_PLAN.md) — define task/model
  suitability, add capability-aware filtering, connect remaining specialist
  roles, and implement bounded runtime failover and availability controls.
- [`EXECUTION_FLOW_ARCHITECTURE.md`](EXECUTION_FLOW_ARCHITECTURE.md) — future
  refactor of session lifecycle, state machine, orchestration, event flow, and
  terminal presentation.
- [`TOOLS.md`](TOOLS.md) — future tool candidates only.
- [`OS_COMMAND_SANDBOX.md`](OS_COMMAND_SANDBOX.md) — OS-level sandbox and
  network/resource isolation for untrusted project code.
- [`LLM_CLARIFICATION_QUESTIONS.md`](LLM_CLARIFICATION_QUESTIONS.md) — pause
  for user clarification and resume the same task.

Project path access control and the initial model-routing layer are delivered;
their remaining enhancements are tracked separately above.
