# ShAI

ShAI is a lightweight terminal AI assistant with two modes:

- **Agent** works with files in a selected project, inspects Git changes, edits code and runs checks.
- **Chat** provides a regular conversation without local project tools.

Shai supports OpenRouter, Gemini, Anthropic, OpenAI, Kilo Gateway (all/non-free and free-only lists), and local Ollama.

> ⚠️ **Disclaimer: This project is actively being developed.** Features, APIs, and behavior may change without notice. Use at your own risk.

## Requirements

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.9+ | Standard library only; no third-party runtime dependencies |
| Git | 2.0+ | Recommended for repository tools (status, diff, commit, push) |
| API Key | — | For at least one supported provider (OpenRouter, Gemini, Anthropic, OpenAI, Kilo, Ollama) |

**Optional (for development/testing):**
- `pytest>=7.0` — only used by `run_all_tests.sh` if installed

## Setup

```sh
# 1. Install optional test dependency (not required for runtime)
python3 -m pip install -r requirements.txt

# 2. Configure API keys
cp setup_keys.sample.sh setup_keys.sh
# Edit setup_keys.sh and add your provider key(s)

# 3. Load keys into current shell
. ./setup_keys.sh

# 4. Optional: configure provider defaults/overrides
cp shai_config.sample.json shai_config.json
# Edit shai_config.json as needed
```

Keep `setup_keys.sh` private; it is ignored by Git.

## Configuration

Shai keeps shared runtime defaults in the installation's `assets/shai_config.json` and provider endpoint overrides in the installation's `assets/provider_config.json`. **Every `.shai/` path is resolved from the currently selected working project, never from the Shai installation/repository.** Switching projects with `/project <path>` changes the active `.shai/` directory for provider/model state, approvals, history, logs, backups, task state, routing, and access grants. Key locations:

- **`assets/model_routing.json` / `.shai/model_routing.json`** — default provider/model plus task-specific model routes
- **agent_safeguards** — history compaction limits, context window estimates
- **tool_safeguards** — per-tool timeouts (git_status, git_diff, etc.)
- **`.shai/model_config.json`** — discovered model limits, capabilities, and user overrides
- **`assets/approval_config.json`** — existing application-wide approval defaults
- **`.shai/approval_config.json`** — only project-specific interactive approval overrides
- **`.shai/os_command_approvals.json`** — interactive OS-command permission grants
- **`assets/shai_config.json`** — retry/backoff, safeguards, and runtime settings
- **project_access_config.json** — generic path access defaults and rules; per-project user grants are saved under `.shai/project_access.json`; automatic edit backups and retention policy live under `.shai/shai_backups/` and `.shai/backup_config.json`

See [Provider configuration](docs/PROVIDERS.md) and [Commands reference](docs/COMMANDS.md) for details.

## Run

Start the agent in the current directory:

```sh
python3 agent.py
```

Or select a project directory:

```sh
python3 agent.py /path/to/project
```

Start chat mode (no project tools):

```sh
python3 chat.py
```

Choose a provider/model when prompted. Shai remembers the last selection. During requests, it shows an estimated context size and, when available, the model context percentage; after a response it shows provider-reported token usage. Use `/help` inside either mode for available commands; `/model` switches provider/model and `/exit` quits.

## Commands (Agent Mode)

| Command | Description |
|---------|-------------|
| `/help` | Show all commands |
| `/model` / `/switch` | Switch provider and model |
| `/status` | Git status (short + branch) |
| `/files` | List project files |
| `/diff` | Show Git diff |
| `/reset` | Clear conversation history |
| `/ask` | Switch to ask mode (chat only, no tools) |
| `/read-only` / `/ro` | Switch to read-only mode (chat with read-only tools) |
| `/code` | Switch to code mode (agent with tools) |
| `/todo` / `/task` | Show current task plan |
| `/commit` | Commit staged changes with generated message (`--message-only` to only generate) |
| `/push` | Push commits to remote |
| `/commit-push` | Commit and push in one command |
| `/project` | Switch project folder |
| `/exec` | Run an allowlisted command profile; not a shell |
| `/exit` | Exit Shai |

Press `Ctrl+D` while Shai is processing to cancel the current iteration and return to the chat prompt.

## Tests

Run the complete test suite from the project root:

```sh
sh ./run_all_tests.sh
```

The runner executes unittest discovery, runs pytest as an additional check when
installed, and compiles the application and test modules. It exits non-zero if
any required check fails. Every run refreshes these reports under `test/`:

- `test/test_report.txt` — readable summary and detailed test output.
- `test/test_report.json` — machine-readable status, counts, timing, and per-test outcomes.
- `test/test_report_pytest.xml` — JUnit XML output when pytest is installed.

`unittest` is the required test runner (stdlib); `pytest` is optional and runs
automatically when installed.

## Documentation

- [Commands reference](docs/COMMANDS.md)
- [Existing tools](docs/TOOLS.md)
- [Project path access control](docs/PROJECT_PATH_ACCESS_CONTROL.md)
- [Project-specific model routing](docs/MODEL_ROUTING.md)
- [Project architecture](docs/ARCHITECTURE.md)
- [Provider configuration](docs/PROVIDERS.md)
- [Terminal output formatting](docs/TERMINAL_OUTPUT.md)
- [Testing and test reports](docs/TESTING.md)
- [Future tools roadmap](docs/plans/TOOLS.md)
- [Remaining model-routing work](docs/plans/MODEL_ROUTING_PLAN.md)
- [shai_command reference](docs/SHAI_COMMAND.md)
- [os_command reference](docs/OS_COMMAND.md)
- [Execution-flow refactoring plan](docs/plans/EXECUTION_FLOW_ARCHITECTURE.md)

## Project Structure

```
agent.py          # Main agent entry point (CLI, modes, commands)
chat.py           # Chat mode entry point
requirements.txt  # Optional test dependencies only
assets/           # Split runtime, provider, command, and access configuration
modules/          # Core modules (tools, commands, history, providers, logging, etc.)
docs/             # Documentation
test/             # Unit tests
```

Generic path policy lives in the installation's `assets/project_access_config.json`; user-approved grants are stored in `<selected-project>/.shai/project_access.json`. Interactive approval overrides, command grants, provider/model selection, model metadata, logs, history, task state and backups all live under `<selected-project>/.shai/`. The installation's `assets/approval_config.json` remains the baseline approval policy. Shai creates `<selected-project>/.shai/.gitignore` to ignore only `logs/`, `shai_backups/`, `task.json`, and `history.json`. Static command policy remains in the installation's `assets/command_config.json`. See `docs/CONFIGURATION.md`.

## License

MIT — see [LICENSE](LICENSE).
