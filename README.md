# Shai

Shai is a lightweight terminal AI assistant with two modes:

- **Agent** works with files in a selected project, inspects Git changes, edits code and runs checks.
- **Chat** provides a regular conversation without local project tools.

Shai supports OpenRouter, Gemini, Anthropic, OpenAI and Kilo Gateway.

## Setup

Requirements: Python 3.9 or newer, and an API key for one supported provider. Git is recommended for repository tools.

```sh
python3 -m pip install -r requirements.txt
cp setup_keys.sample.sh setup_keys.sh
```

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

Choose a provider/model when prompted. Shai remembers the last selection. Use `/help` inside either mode for available commands; `/model` switches provider/model and `/exit` quits.

## Tests

```sh
sh ./run_all_tests.sh
```

`unittest` is the required test runner. `pytest` is optional and is run when installed.

## Documentation

- [Existing tools](docs/TOOLS.md)
- [Project architecture](docs/ARCHITECTURE.md)
- [Future tools roadmap](docs/roadmap/TOOLS_ROADMAP.md)
