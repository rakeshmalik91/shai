# Shai — terminal coding assistant and chat

Shai is a lightweight, terminal-based coding assistant for working with a local project. It can inspect files, search source, review Git state, propose edits, and run project checks. A separate chat mode provides general conversation without project tools.

The code is designed for Python 3.8+ and uses the Python standard library. `pytest` is optional; the built-in `unittest` runner is sufficient.

## Features

- **Two modes:** `agent.py` for project-aware coding and `chat.py` for general chat.
- **Interactive provider/model selection:** provider first, then model, on every startup.
- **Remember last selection:** provider option `0` reuses the saved provider/model pair.
- **Providers:** OpenRouter, Gemini, Anthropic, OpenAI, Kilo Gateway (all listed models), and Kilo Gateway (free models only).
- **In-session model switching:** `/model`, `/switch`, or `/switch-model` in both modes.
- **Project tools:** list/read/search files, Git status/diff, edit files, and run checks.
- **Explicit file pagination:** `read_file` requires `path`, zero-based `offset`, and `limit`; each result provides the exact `next_offset` for the next chunk. Every requested read executes against the current file instead of being replaced by a synthetic duplicate-suppression response. Tool calls display their effective parameters before execution.
- **Advisory repeat verification:** exact tool name and complete parameters are tracked for diagnostics; repeated successful calls are allowed, and invalid arguments do not permanently disable a tool.
- **Approval control:** approve individual actions or choose `a` / `all` to persist `yes_to_all`.
- **Iteration controls:** configurable maximum tool-call iterations and Ctrl+D/Ctrl+C interruption in agent mode.
- **Loop protection:** duplicate tool-call detection, repeat-call warnings, and bounded recovery from invalid tool names.
- **API retry policy in agent mode:** configurable retry count, exponential backoff, jitter, network/HTTP retry conditions, and invalid-JSON retries. HTTP 400 is never blindly retried.
- **Invalid provider tool-call recovery:** detects provider errors such as `invalid tool call provided`, repairs OpenAI/Anthropic tool history, preserves user requests and tool results, and regenerates within a separate recovery limit.
- **Command timeouts:** configurable limits for test commands and Git status/diff.

## Requirements

- Python 3.8 or newer.
- An API key for the provider you want to use.
- A POSIX-compatible shell for `run_all_tests.sh` (the script uses `#!/bin/sh`, so Bash is not required).

No `pip install` is required to run Shai. `pytest` is optional and is only used by the test script if already installed.

## Installation and layout

Keep these files and folders together:

```text
shai/
├── agent.py
├── chat.py
├── shai_config.json
├── modules/
│   ├── __init__.py
│   └── provider_selection.py
├── test/
│   ├── test_agent.py
│   ├── test_chat.py
│   └── test_provider_selection.py
├── run_all_tests.sh
└── README.md
```

`agent.py` and `chat.py` both import `modules/provider_selection.py`. Do not move that module away from the `modules` directory.

## API keys

Export the key for the provider you plan to use in the current shell. For example:

```sh
export OPENROUTER_API_KEY='your-key'
export GEMINI_API_KEY='your-key'
export ANTHROPIC_API_KEY='your-key'
export OPENAI_API_KEY='your-key'
export KILO_API_KEY='your-key'
```

You only need to set the key for the provider you select. Avoid putting real keys in `shai_config.json` or committing them to Git. If you use a `setup_keys.sh` helper, source it so its exported variables remain in the current shell:

```sh
. ./setup_keys.sh
```

Provider key pages:

- OpenRouter: <https://openrouter.ai/keys>
- Gemini: <https://aistudio.google.com/apikey>
- Anthropic: <https://console.anthropic.com/settings/keys>
- OpenAI: <https://platform.openai.com/api-keys>
- Kilo Gateway: <https://app.kilo.ai>

## Run Shai

From the Shai directory:

```sh
# Project-aware coding agent, using the current directory as the project
python3 agent.py

# Project-aware coding agent for a different project directory
python3 agent.py /path/to/project

# General conversation, without local project tools
python3 chat.py
```

On every startup, Shai asks you to select a provider and then a model. The provider menu includes:

- `0` — use the last saved provider and model (no second model prompt)
- OpenRouter
- Gemini
- Anthropic
- OpenAI
- Kilo Gateway — all models returned by its model catalogue
- Kilo Gateway — only model IDs ending in `:free`

The selected provider and model are saved in `shai_config.json`. The API key is read from the corresponding environment variable.

### Switch model without restarting

In either agent or chat mode, enter:

```text
/model
```

Aliases:

```text
/switch
/switch-model
```

Shai asks for a provider, then a model. It saves the new selection and continues. Readable text conversation history is retained; provider-specific tool-call records are discarded when switching in agent mode.

### Agent commands

| Command | Action |
|---|---|
| `/help` | Show commands and usage |
| `/model` | Switch provider and model |
| `/switch`, `/switch-model` | Aliases for `/model` |
| `/status` | Show Git status |
| `/files` | List project files |
| `/diff` | Show Git diff |
| `/reset` | Clear conversation history |
| `/exit` | Exit Shai |

### Chat commands

| Command | Action |
|---|---|
| `/help` | Show chat commands |
| `/model` | Switch provider and model |
| `/switch`, `/switch-model` | Aliases for `/model` |
| `/status` | Show current provider and model |
| `/reset` | Clear conversation history |
| `/exit` | Exit Shai |

### Interrupt an agent iteration

While the agent is processing a request, press **Ctrl+D** to cancel the current iteration and return to the `You:` prompt. **Ctrl+C** also interrupts the current iteration. If a test/check subprocess is running, Shai terminates that subprocess. An API request already sent to the provider may still finish remotely, but Shai stops waiting for it.

At the normal input prompt, Ctrl+D/EOF exits the program.

## Configuration

`shai_config.json` stores provider/model selection and agent behaviour. A representative configuration is:

```json
{
  "provider": "kilo-free",
  "model": "cohere/north-mini-code:free",
  "max_tool_call_iterations": 100,
  "yes_to_all": true,
  "tool_safeguards": {
    "duplicate_call_detection": true,
    "duplicate_call_limit": 2,
    "repeat_call_warning": true,
    "repeat_call_warning_after": 2,
    "run_checks_timeout_seconds": 120,
    "git_status_timeout_seconds": 15,
    "git_diff_timeout_seconds": 15
  },
  "retry": {
    "enabled": true,
    "max_retries": 3,
    "initial_backoff_seconds": 1.0,
    "backoff_multiplier": 2.0,
    "max_backoff_seconds": 20.0,
    "jitter": true,
    "jitter_fraction": 0.2,
    "retry_network_errors": true,
    "retry_http_statuses": [408, 425, 429, 500, 502, 503, 504],
    "retry_invalid_json": true,
    "invalid_tool_selection_enabled": true,
    "invalid_tool_selection_max_attempts": 3,
    "invalid_tool_call_recovery": {
      "enabled": true,
      "max_recovery_attempts": 2,
      "repair_conversation_history": true,
      "preserve_user_request": true,
      "stop_on_repeated_failure": true,
      "max_tool_result_chars": 12000
    }
  }
}
```

### Main settings

| Setting | Meaning |
|---|---|
| `provider` | Last selected provider ID |
| `model` | Last selected model ID |
| `max_tool_call_iterations` | Maximum model/tool rounds for one agent request; must be a positive integer |
| `yes_to_all` | Whether approval prompts are automatically accepted after choosing `a` / `all` |

### `tool_safeguards`

| Setting | Meaning |
|---|---|
| `duplicate_call_detection` | Block repeated identical tool calls when enabled |
| `duplicate_call_limit` | Number of identical executions allowed per request before blocking the next one |
| `repeat_call_warning` | Attach a warning to repeated-call results |
| `repeat_call_warning_after` | Execution count at which the warning begins |
| `run_checks_timeout_seconds` | Maximum runtime for test/compile checks |
| `git_status_timeout_seconds` | Git status timeout |
| `git_diff_timeout_seconds` | Git diff timeout |

### `retry` (agent mode)

| Setting | Meaning |
|---|---|
| `enabled` | Master switch for API retries |
| `max_retries` | Retries after the initial failed API request; `0` disables retries |
| `initial_backoff_seconds` | Delay before the first retry |
| `backoff_multiplier` | Exponential multiplier for each subsequent retry; values below `1` are clamped to `1` |
| `max_backoff_seconds` | Upper bound for the calculated backoff |
| `jitter` | Add a random delay to the calculated backoff |
| `jitter_fraction` | Maximum jitter as a fraction of the calculated delay, from `0` to `1` |
| `retry_network_errors` | Retry network/URL errors |
| `retry_http_statuses` | HTTP status codes eligible for retry. HTTP 400 is always excluded, even if listed here. |
| `retry_invalid_json` | Retry responses that cannot be decoded as JSON |
| `invalid_tool_selection_enabled` | Stop an agent request after repeated invalid tool selections |
| `invalid_tool_selection_max_attempts` | Invalid tool selection attempts allowed before recovery stops the request |
| `invalid_tool_call_recovery` | Separate recovery policy for provider errors rejecting tool calls |

With the default retry values, the backoff grows exponentially, capped by `max_backoff_seconds`, plus optional jitter. Actual waits may vary slightly when jitter is enabled.

#### `retry.invalid_tool_call_recovery`

A provider error such as `invalid tool call provided` is not treated as a transient API failure. Shai immediately skips normal HTTP retries and starts a separate recovery path:

1. Detect the provider's invalid-tool-call error (including nested gateway error details).
2. Remove provider-specific tool protocol records from conversation history. OpenAI `assistant.tool_calls` / `tool` messages and Anthropic `tool_use` / `tool_result` blocks are converted to ordinary text so the malformed exchange is not resubmitted.
3. Preserve the original user requests and previous tool results (tool-result text is bounded by `max_tool_result_chars`).
4. Add a recovery instruction listing the exact available tools and regenerate the response.
5. Stop after the configured recovery attempts, or earlier if another failure occurs without any further history that can be repaired.

| Setting | Meaning |
|---|---|
| `enabled` | Enable provider-side invalid tool-call recovery |
| `max_recovery_attempts` | Maximum regenerated requests after an invalid tool-call error; separate from normal API retries |
| `repair_conversation_history` | Convert/remove provider-specific tool protocol records before regenerating |
| `preserve_user_request` | Reserved safety preference; original user requests are always retained |
| `stop_on_repeated_failure` | Stop if a repeated rejection occurs and no additional malformed history can be repaired |
| `max_tool_result_chars` | Maximum characters retained per previous tool result during repair |

**HTTP 400 is never retried**, even if a user adds it to `retry_http_statuses`. For the specific `invalid tool call provided` error, Shai attempts history repair instead. Other HTTP 400 errors are reported immediately because repeating the same request is unlikely to help.

If recovery is exhausted, Shai returns to the prompt, retains the original user request, and leaves a concise assistant note explaining that recovery failed. Use `/model` to switch provider/model or rephrase the request.

Changing provider/model updates only those two keys and preserves the rest of the configuration.

## Safety boundaries

- File tools resolve paths under the selected project root and reject paths outside it.
- Access to `.git`, `.env*`, `credentials.json`, and `secrets.json` is blocked by the path guard.
- File edits display a proposed diff and require approval unless `yes_to_all` is enabled.
- Test/check commands require approval unless `yes_to_all` is enabled.
- Test/check commands run with a timeout; Ctrl+D can interrupt them in agent mode.
- Duplicate tool-call detection is scoped to one user request.

## Tests

The project includes tests for configuration persistence/validation, transient retries, HTTP 400 non-retry behaviour, provider invalid-tool-call detection, OpenAI/Anthropic history repair, recovery regeneration, tool-name validation, duplicate-call protection, provider/model selection, Kilo free-model filtering, provider-switch history handling, and Anthropic response parsing.

Run the complete suite from the project directory:

```sh
sh ./run_all_tests.sh
```

If the executable bit is preserved, this also works:

```sh
./run_all_tests.sh
```

The script is POSIX `sh` compatible and runs:

1. `unittest` tests from `test/test_*.py` (required; standard library).
2. `pytest` tests if pytest is already installed (optional, so tests are run a second way).
3. Python syntax compilation for the agent, chat client, modules, and tests.

Run only the built-in unit tests:

```sh
python3 -m unittest discover -s test -p 'test_*.py' -v
```

## Troubleshooting

**`API key is not set`** — export the matching provider key in the shell from which you launch Shai. If using a setup script, source it with `. ./setup_keys.sh`.

**No models are listed** — check the key and provider account access. Kilo Free filters the returned model catalogue to IDs ending in `:free`.

**`/model` is not recognized** — make sure you are running the updated `agent.py` or `chat.py` together with `modules/provider_selection.py`.

**Tests fail to import `modules`** — run the test script from the project copy, and keep the `modules/` directory beside `agent.py` and `chat.py`.

**A check times out** — increase the corresponding timeout under `tool_safeguards`, or reduce the scope of the check.

**The provider reports `invalid tool call provided`** — Shai skips HTTP retries and attempts to repair the conversation history. If recovery is exhausted, switch model with `/model` or rephrase the request. `retry.invalid_tool_call_recovery.max_recovery_attempts` controls regeneration attempts.

**HTTP 400 repeats** — HTTP 400 is intentionally excluded from automatic retries. Check the displayed error; only recognized invalid-tool-call errors use automatic history repair.

## License

MIT License. Add or retain the project's `LICENSE` file when distributing the source.


## Agent-loop and context safeguards

Shai bounds conversation history and tool output to avoid runaway API payloads.
The settings are shared by agent and chat modes:

```json
"agent_safeguards": {
  "enabled": true,
  "max_history_chars": 90000,
  "max_tool_result_chars": 8000,
  "keep_recent_turns": 4,
  "max_recent_tool_cycles": 6,
  "max_consecutive_no_progress_iterations": 3,
  "max_tool_calls_per_request": 80,
  "validate_tool_arguments": true,
  "reject_unknown_tool_arguments": true
}
```

- `max_history_chars` bounds the serialized conversation sent to the provider.
  Older turns are summarized and old tool-call cycles are removed as complete
  units; the latest user request is retained.
- `max_tool_result_chars` limits each saved tool result while preserving its
  beginning and end.
- `max_consecutive_no_progress_iterations` stops repeated tool-call batches or
  consecutive tool-error iterations.
- `max_tool_calls_per_request` places a hard ceiling on tool executions in a
  single user request.
- Tool arguments are checked against the same JSON schemas sent to the provider.
  Unknown parameters and incorrect types are rejected before tool execution.
- A provider response ending with `length` or `max_tokens` (including a native
  finish reason) stops the current request immediately. Shai never executes
  incomplete tool arguments or blindly resends the same oversized request.

The `patch_file` tool is registered for execution and uses its exact
`path`, `old_text`, and `new_text` schema.

## Agent tool-use safeguards

`read_file` supports line-based pagination. `offset` is a zero-based line offset
and `limit` is the maximum number of lines to return. Large-file results include
a `next_offset` instruction so the agent can continue without repeatedly loading
the entire file.

The agent also suppresses duplicate file-chunk reads while the previous result
is still in conversation history. If history compaction has removed that result,
the file chunk can be read again. Invalid arguments are tracked per tool; after
the configured number of failures, that tool is disabled for the rest of the
current request. Successful new tool work resets the no-progress counter, while
repeated batches and changed-but-still-invalid calls advance it.

Example `agent_safeguards` settings:

```json
"agent_safeguards": {
  "max_invalid_argument_attempts_per_tool": 2,
  "default_read_file_lines": 100,
  "max_read_file_lines": 500,
  "max_read_file_chars": 4000,
  "deduplicate_read_file_calls": true
}
```

## Tool-call visibility and repeat verification

Shai displays each tool call's parameters in the terminal, including `read_file`
pagination (`path`, `offset`, and `limit`). Configure this under
`tool_call_display` in `shai_config.json`:

```json
"tool_call_display": {
  "show_parameters": true,
  "max_argument_chars": 2000
}
```

Repeat verification uses the tool name and the complete normalized parameter
set. Different pages or parameter values are distinct operations. Repeated
successful calls are allowed; repeat warnings are informational. An identical
`read_file` page may reuse its cached content while that content remains in
conversation history. Invalid arguments are rejected with schema guidance, but
do not permanently disable the tool.

