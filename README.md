# Shai

Shai is a lightweight terminal AI assistant with two modes:

- **Agent** works with files in a selected project, inspects Git changes, edits code and runs checks.
- **Chat** provides a regular conversation without local project tools.

Shai supports OpenRouter, Gemini, Anthropic, OpenAI, Kilo Gateway (all/non-free and free-only lists), and local Ollama.

## Setup

Requirements: Python 3.9 or newer, and an API key for one supported provider. Git is recommended for repository tools.

```sh
python3 -m pip install -r requirements.txt
cp setup_keys.sample.sh setup_keys.sh
cp shai_config.sample.json shai_config.json  # optional provider defaults/overrides\n```

Edit `setup_keys.sh`, add your provider key, then load it in the current shell:

```sh
. ./setup_keys.sh
```

Keep `setup_keys.sh` private; it is ignored by Git.

## Run

Start the agent in the current directory:

```sh
python3 agent.py
```

Or select a project directory:

```sh
python3 agent.py /path/to/project
```

Start chat mode:

```sh
python3 chat.py
```

Choose a provider/model when prompted. Provider endpoints, authentication, headers and timeouts are configurable in `shai_config.json`; see [Provider configuration](docs/PROVIDERS.md). Shai remembers the last selection. During requests, it shows an estimated context size and, when available, the model context percentage; after a response it shows provider-reported token usage. Use `/help` inside either mode for available commands; `/model` switches provider/model and `/exit` quits.

## Tests

```sh
sh ./run_all_tests.sh
```

`unittest` is the required test runner. `pytest` is optional and is run when installed.

## Documentation

- [Commands reference](docs/COMMANDS.md)
- [Existing tools](docs/TOOLS.md)
- [Project architecture](docs/ARCHITECTURE.md)
- [Future tools roadmap](docs/roadmap/TOOLS_ROADMAP.md)


During agent/code requests, Shai keeps a pinned live status area at the bottom
of interactive terminals. Tool activity, context-window usage, and the task
checklist update in place while normal conversation output scrolls above it.


## Terminal colours (iSH / ANSI)

Shai uses ANSI colours on interactive terminals. User prompts are green,
assistant replies cyan, tool activity magenta, tool batch counts yellow,
system notices blue, and errors red. Tool calls still update one in-place
activity line; ordinary chat continues to scroll normally.

Colour detection is automatic for TTY output. Disable colours with
`NO_COLOR=1`, `TERM=dumb`, or `SHAI_COLOR=0`; force-enable on a compatible
terminal with `SHAI_COLOR=1` when automatic TTY detection is insufficient.
