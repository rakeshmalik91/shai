# Existing Tools

The tools below are available in **Agent mode**. Chat mode does not access the local project.

## Project discovery and reading

| Tool | Parameters | Purpose |
|---|---|---|
| `list_files` | `path` | List project files under a directory. |
| `read_file` | `path`, `offset`, `limit` | Read a UTF-8 file in line-based chunks. `offset` is zero-based; use the returned `next_offset` to continue. |
| `search_files` | `query`, `path` | Case-insensitive text search in one file or recursively in a directory. |

## Code intelligence and project understanding

The eight priority code-understanding and workflow tools are now implemented and available to Agent mode:

| Tool | Parameters | Purpose |
|---|---|---|
| `get_file_outline` | `path` | Use Python's AST to list imports, top-level classes/functions and class methods with line ranges. Python files only. |
| `find_symbol` | `symbol`, `path` | Find Python definitions of classes, functions, methods and simple assignments by exact name. |
| `find_references` | `symbol`, `path` | Find textual occurrences of a symbol. This is text search, not semantic/LSP reference analysis. |
| `get_project_map` | `path` | Summarize project directories, file types and likely Python entry points. |
| `get_test_map` | `path` | List test files and heuristically suggest matching source modules. Mapping is approximate. |
| `update_task_plan` | `steps` | Set the in-memory task checklist. Each step has a `title` and a status: `pending`, `in_progress`, `completed` or `blocked`. |

The eight priority tools are `get_file_outline`, `find_symbol`, `find_references`, `verify_changes`, `inspect_test_failure`, `get_project_map`, `get_test_map` and `update_task_plan`. `replace_lines` is an additional editing tool.

## Editing

| Tool | Parameters | Purpose |
|---|---|---|
| `edit_file` | `path`, `content` | Propose a whole-file replacement with a diff preview. If a model mistakenly sends `old_text` and `new_text` under this tool name, Shai automatically routes it to `patch_file`. |
| `patch_file` | `path`, `old_text`, `new_text` | Replace one exact text block, only when it matches exactly once. |
| `replace_lines` | `path`, `start_line`, `end_line`, `new_text` | Replace a 1-based inclusive line range. Useful when exact matching fails because of indentation or whitespace. |

File modifications are previewed and require approval according to the configured approval settings. Risky changes require explicit approval. Original files are backed up before atomic replacement.

**Editing guidance:** read the current file before editing. If `patch_file` reports zero or multiple matches, do not repeat the same patch. Re-read the relevant lines and use `replace_lines` with the current line numbers.

## Git inspection

| Tool | Parameters | Purpose |
|---|---|---|
| `git_status` | none | Show current branch and short working-tree status. |
| `git_diff` | none | Show unstaged changes. |

These tools do not commit, push or rewrite Git history.

## Verification and debugging

| Tool | Parameters | Purpose |
|---|---|---|
| `run_checks` | `check`, `path` | Run `pytest`, `unittest` or `compileall` with configured timeout/output bounds. |
| `verify_changes` | `path`, `test_check` | Run Python compilation and optionally `unittest` or `pytest` as a combined verification pass. |
| `inspect_test_failure` | `output` | Extract likely failing tests, assertions, exception types and traceback context from supplied output. It does not run tests. |

Verification and command execution require approval unless the configured session approval setting permits them. Execution is bounded by configured timeout and output limits.

## Safeguards and supporting capabilities

These are internal capabilities rather than model-callable tools:

- Project-root path validation and protection for sensitive paths.
- Tool argument validation and parameter-aware repeat detection.
- Detection of repeated identical failed calls.
- Bounded tool output and conversation-history compaction.
- Bounded test-process execution, timeout and interruption handling.
- File-change previews and backups.
- Provider-specific reasoning-summary extraction.
- Run-based diagnostic logs with sensitive API payload fields redacted.

## Configuration

`shai_config.json` controls provider/model selection, approval behaviour, safeguards, test execution, logging, history limits and display settings. API keys belong in environment variables, not this file.

## Context meter

Before each request, Agent and Chat display an approximate prompt-token count. The estimate is also shown in the thinking indicator. After the response, Shai displays exact prompt/completion/total token counts when the provider returns usage metadata.

A context percentage is shown only when a context limit is known. Shai captures model context metadata from the provider's model catalogue when available and stores it in `shai_config.json`. If the provider does not publish a limit, the display explicitly says the limit is unknown rather than inventing a percentage. A user may add `context_window_tokens` for the currently selected model or an entry in `model_context_windows` keyed as `provider::model`.

The pre-request estimate is heuristic and should not be treated as tokenizer-exact. Debug logs record both estimates and provider-reported usage.
