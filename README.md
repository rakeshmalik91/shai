# Shai - Autonomous Coding Assistant

Shai is a local, terminal-based coding assistant that works directly with your project files. It can explore your codebase, make edits, run tests, and help you understand and modify your code.

## Features

- **Project-aware**: Reads and understands your local project structure
- **File operations**: List, read, search, and edit files with your approval
- **Git integration**: View status and diffs
- **Test execution**: Run pytest, unittest, or compileall checks
- **Multi-provider support**: Works with OpenRouter (free models) and Google Gemini
- **Safety first**: All file edits and command executions require explicit user approval
- **No external dependencies**: Pure Python standard library (except API calls)

## Quick Start

### Prerequisites

- Python 3.8+
- An API key for either:
  - **OpenRouter** (free models available) - set `OPENROUTER_API_KEY`
  - **Google Gemini** - set `GEMINI_API_KEY`

### Installation

```bash
# Clone or copy the project files
# No pip install needed - uses only standard library
```

### Configuration

Set your API key as an environment variable:

```bash
# For OpenRouter (recommended - has free models)
export OPENROUTER_API_KEY="your-key-here"

# Or for Gemini
export GEMINI_API_KEY="your-key-here"
```

### Running Shai

```bash
# Run in current directory
python3 shai.py

# Run in a specific project directory
python3 shai.py /path/to/project

# Specify provider explicitly (openrouter or gemini)
python3 shai.py . openrouter
python3 shai.py . gemini
```

On first run, Shai will fetch available models and ask you to choose one. Your selection is saved in `shai_config.json` for future runs.

## Usage

### Interactive Commands

| Command | Description |
|---------|-------------|
| `/help` | Show help and examples |
| `/status` | Show Git status |
| `/files` | List project files |
| `/diff` | Show unstaged Git changes |
| `/reset` | Clear conversation history |
| `/exit` | Exit Shai |

### Natural Language Requests

Just type what you want in plain English:

```
You: Explain the project structure.
You: Find where the login screen is implemented.
You: Fix the bug in main.py where it crashes on empty input.
You: Add error handling to the API client.
You: Review my current Git changes.
You: Run the Python tests.
```

### How It Works

1. **You ask** - Type a request or question
2. **Shai explores** - Uses tools to read files, search code, check git status
3. **Shai proposes** - Shows you a diff for any file changes
4. **You approve** - Type `y` to apply changes, `n` to reject
5. **Shai verifies** - Can run tests to confirm changes work

## Safety & Privacy

- **No file access outside project directory** - Path traversal is blocked
- **No secrets exposure** - `.env`, `credentials.json`, `secrets.json` are blocked
- **No auto-execution** - All edits and commands require your explicit approval
- **Local first** - Your code never leaves your machine except for API calls to the LLM provider

## Configuration File

`shai_config.json` stores your preferred provider and model:

```json
{
  "provider": "openrouter",
  "model": "nvidia/nemotron-3-ultra-550b-a55b:free"
}
```

Delete this file to re-run the model selection.

## Project Structure

```
.
├── shai.py           # Main agent (entry point)
├── shai-chat.py      # Simpler Gemini chat alternative
├── shai_config.json  # Saved provider/model preferences
├── models.json       # Model metadata (if present)
├── setup_keys.sh     # Helper script for API key setup
├── gemini_test.py    # Test script for Gemini API
└── README.md         # This file
```

## Providers & Models

### OpenRouter (Default)
- Access to many free models (look for `:free` suffix)
- Models: `cohere/north-mini-code:free`, `nvidia/nemotron-3-ultra-550b-a55b:free`, etc.
- Get a key at: https://openrouter.ai/keys

### Google Gemini
- Models: `gemini-2.5-flash`, `gemini-2.5-pro`, etc.
- Get a key at: https://aistudio.google.com/apikey

## Troubleshooting

**"API key not set"**
- Ensure `OPENROUTER_API_KEY` or `GEMINI_API_KEY` is exported in your shell

**"No models available"**
- Check your API key is valid and has access to models
- For OpenRouter, ensure you have credits or are using free models

**"Path is outside the project directory"**
- Shai only works within the project root you specified

**Permission denied on edit**
- Ensure you have write permissions to the project files

## License

MIT License - see LICENSE file for details.