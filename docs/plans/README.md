# ShAI Implementation Plans

Only unfinished work and forward-looking technical roadmaps belong in this directory.
Implemented feature references and current architecture contracts live in [`../`](../):

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — system structure, agent loop, shared modules, and persistent storage.
- [`../PROJECT_STRUCTURE.md`](../PROJECT_STRUCTURE.md) — repository directory tree and module boundaries.
- [`../SHAI_COMMAND.md`](../SHAI_COMMAND.md) — deterministic natural-language routing for Shai slash commands.
- [`../OS_COMMAND.md`](../OS_COMMAND.md) — allowlisted, non-shell operating system command execution and safety profile.
- [`../PROJECT_PATH_ACCESS_CONTROL.md`](../PROJECT_PATH_ACCESS_CONTROL.md) — project path permissions, glob rules, and interactive user grants.
- [`../MODEL_ROUTING.md`](../MODEL_ROUTING.md) — project-scoped task-role routing, priority resolution, and availability caching.
- [`../TOOLS.md`](../TOOLS.md) — delivered tool inventory, parameter schemas, and behavioral contracts.
- [`../TERMINAL_OUTPUT.md`](../TERMINAL_OUTPUT.md) — ANSI color conventions and transient in-place tool status updates.
- [`../CONFIGURATION.md`](../CONFIGURATION.md) — split configuration schema and persistence responsibilities.
- [`../TESTING.md`](../TESTING.md) — automated test runner and durable report generation.

## Remaining Plans and Roadmaps

1. [`EXECUTION_FLOW_ARCHITECTURE.md`](EXECUTION_FLOW_ARCHITECTURE.md)
   - Architectural roadmap for session lifecycle, explicit state machine transitions, event bus, normalized tool dispatch, and decoupling presentation from execution.
   - *Progress note:* Engine domain module extraction into `modules/engine/` (`configuration.py`, `file_editing.py`, `git_operations.py`, `history_recovery.py`, `model_orchestration.py`, `process_control.py`, `project_inspection.py`, `provider_api.py`, `task_plan.py`, `tool_execution.py`, `tool_presentation.py`, and the `agent_runtime.py` facade) is completed. Subsequent phases focus on session state isolation and the event bus.

2. [`MODEL_ROUTING_PLAN.md`](MODEL_ROUTING_PLAN.md)
   - Capability-aware model filtering (context window, tool support, modality, structured output, data privacy).
   - Candidate selection algorithm and version 2 configuration schema.
   - Bounded runtime failover and failure classification across candidate models.
   - Availability cache invalidation semantics, cooldowns, and specialist role call-site wiring (`planner`, `code`, `debug`, `test`, `review`, `documentation`, `research`).

3. [`TOOLS.md`](TOOLS.md)
   - Detailed technical specifications for future tool candidates: `search_logs`, `check_environment`, `get_import_graph`, `git_log`, `git_blame`, `compare_files`, `apply_patch`, persistent task plans, and semantic code navigation.
   - Parameter schemas, error behaviors, access policy enforcement, and output limits.

4. [`OS_COMMAND_SANDBOX.md`](OS_COMMAND_SANDBOX.md)
   - Platform-specific sandboxing (Linux user namespaces/bwrap, macOS sandbox-exec, iSH/container isolation).
   - Project-only filesystem mounts, network denial for child processes (especially Python tests), resource quotas, process-tree cleanup, and approval previews.

5. [`LLM_CLARIFICATION_QUESTIONS.md`](LLM_CLARIFICATION_QUESTIONS.md)
   - Specification for `ask_user` tool interaction.
   - Request suspension and resumption state machine, conversational prompt handling, choice presentation, and loop-protection budgets.

6. [`TESTING_STRATEGY.md`](TESTING_STRATEGY.md)
   - Quality and test hardening roadmap: exhaustive unit coverage across configuration and tool layers, fault injection, edge-case testing (boundary values, corruption, Unicode, symlinks), and non-interactive determinism.
