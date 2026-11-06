# Configuration layout

Shai configuration is stored under `assets/` and split by responsibility:

- `assets/shai_config.json` — core agent, runtime, display, retry, approval, and safeguard settings.
- `assets/provider_config.json` — provider endpoint/authentication overrides under `providers`.
- `assets/command_config.json` — Shai slash-command and OS command settings (`shai_command`, `os_command`).
- `assets/context_window_config.json` — per-model context limits and active context token limit.
- `assets/cli_config.json` — terminal/CLI presentation, tool-call display, reasoning display, and file-change display.
- `assets/logging_config.json` — log level, output location, rotation, retention, and console logging.
- `assets/command_registry.json` — natural-language command registry.

Runtime configuration loaders merge the split files for consumers that need a unified view. Provider/model selection metadata remains in the core configuration; provider-specific endpoint and authentication overrides belong in `provider_config.json`. Context-window values, CLI presentation settings, and logging settings are persisted only to their dedicated files.
