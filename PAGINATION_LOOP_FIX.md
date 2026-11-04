# Pagination, repeated-tool and reasoning-debug fixes

## What changed

- `search_files` accepts either a single project file or a directory. Searching `agent.py` no longer returns `Not a directory`; directory searches remain recursive.
- The `search_files` tool schema explains both supported path forms. Kilo receives the full canonical tool descriptions instead of shortened descriptions that discard usage guidance.
- Tool calls show a short action label and their effective arguments before execution. Tool errors are printed in the UI instead of being hidden in the conversation protocol.
- Three consecutive executions of the exact same tool name and parameters that all fail now stop the current request with a visible diagnostic. Successful repeated calls and calls with different parameters remain allowed.
- Provider-designated reasoning summaries are written in full to DEBUG logs and can still be displayed in the UI. Raw private reasoning / chain-of-thought fields are intentionally excluded from logs and UI; API payload logging continues to redact those fields.
- Existing file-only logging remains in place; DEBUG output does not go to the terminal.

## Verification

- `python -m compileall -q .`
- `python -m unittest discover -s test -v`
- `PYTHONPATH=. pytest -q`

All 76 tests pass.
