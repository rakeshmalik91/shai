# Shai

Shai is a lightweight, terminal-based AI assistant with two modes:

- **Agent** — explores and edits a local project, works with Git, and runs checks.
- **Chat** — a conversational assistant without local project tools.

It supports OpenRouter, Gemini, Anthropic, OpenAI, and Kilo Gateway.

## Requirements

- Python 3.9 or newer
- Git (recommended for project status and diff tools)
- An API key for at least one supported provider

Runtime code uses the Python standard library. `pytest` is optional and is used by the extended test run.

## Setup

1. Install Python and Git.
2. Install the optional test dependency:

   ```sh
   python3 -m pip install -r requirements.txt
   ```

3. Create your local API-key file:

   ```sh
   cp setup_keys.sample.sh setup_keys.sh
   ```

4. Edit `setup_keys.sh` and add the key for your provider.
5. Load the keys in your shell:

   ```sh
   . ./setup_keys.sh
   ```

Keep `setup_keys.sh` private; do not commit it.

## Run

Start the coding agent in the current directory:

```sh
python3 agent.py
```

Or point it at a project directory:

```sh
python3 agent.py /path/to/project
```

Start chat mode:

```sh
python3 chat.py
```

At startup, choose a provider and model. Shai saves the selection for the next launch.

In either mode, use `/help` for commands, `/model` to change provider/model, `/status` to inspect the selection, `/reset` to clear the current conversation, and `/exit` to quit. Agent mode also provides `/files` and `/diff`.

## Tests

Run the full test and syntax-check script:

```sh
sh ./run_all_tests.sh
```

The required test suite uses `unittest`. If `pytest` is installed, the script also runs the pytest suite.

## Documentation

- [Existing tools](TOOLS.md)
- [Project architecture](ARCHITECTURE.md)
- [Future roadmap](ROADMAP.md)
