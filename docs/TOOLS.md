# Existing Tools

This document describes the tools currently exposed by Shai's coding agent in `agent.py`. Chat mode does not expose local project tools.

## Project discovery and reading

| Tool | Parameters | What it does |
|---|---|---|
| `list_files` | `path` | Lists project files under a directory. Skips common generated/dependency directories and limits the result to 300 entries. |
| `read_file` | `path`, `offset`, `limit` | Reads a UTF-8 file in line-based chunks. `offset` is zero-based; `limit` is the number of lines (up to 500). The result indicates the next offset when more content remains. |
| `search_files` | `query`, `path` | Performs a case-insensitive text search. `path` can be one file or a directory for recursive search. Results are capped at 100 matches. |

## Editing

| Tool | Parameters | What it does |
|---|---|---|
| `edit_file` | `path`, `content` | Proposes replacing a file's contents. Shai validates the path/content and shows a change preview. Approval behaviour is controlled by the agent configuration. |
| `patch_file` | `path`, `old_text`, `new_text` | Replaces one exact, unique text block in a file. Intended for targeted edits rather than rewriting an entire file. |

## Git inspection

| Tool | Parameters | What it does |
|---|---|---|
| `git_status` | none | Shows the current branch and short Git working-tree status. |
| `git_diff` | none | Shows unstaged changes. |

These tools inspect repository state; they do not commit, push, or alter Git history.

## Checks

| Tool | Parameters | What it does |
|---|---|---|
| `run_checks` | `check`, `path` | Runs one of the supported checks in the selected directory: `pytest`, `unittest`, or `compileall`. Execution is bounded by configured timeout/output limits and requires approval unless approval has been pre-authorized in configuration. |

## Agent safeguards and supporting capabilities

The following are internal agent capabilities, not separate model-callable tools:

- **Argument validation:** validates tool names and arguments against the declared tool schemas.
- **Repeat/no-progress tracking:** records tool-call signatures and detects repeated error patterns; legitimate calls with different parameters remain distinct.
- **Bounded tool output and history:** limits oversized tool results and compacts older conversation history.
- **File access boundaries:** resolves paths within the selected project root and applies file-size limits.
- **Bounded command execution:** applies timeouts, output caps, interruption handling, and child-process termination to supported checks.
- **Change previews:** displays bounded diffs before file changes.
- **Conversation history:** stores agent and chat histories separately in `.shai/history.json`.
- **Provider reasoning summaries:** extracts supported provider-supplied summaries for display and debugging.
- **Logging:** writes configurable run logs, including diagnostic details. API payload logging redacts configured sensitive fields.

## Configuration

`shai_config.json` controls the selected provider/model, tool safeguards, retry behaviour, test execution limits, logging, change previews, and reasoning display. Keep API keys in environment variables rather than in this configuration file.
