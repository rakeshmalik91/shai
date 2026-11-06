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

File modifications are previewed and require approval according to the granular
`approve` settings in `assets/shai_config.json`. Risky truncations/deletions require an
explicit category or tool approval (or an interactive confirmation); the global
`approve.all` setting alone does not bypass high-risk file checks. Original files
are backed up before atomic replacement.

**Editing guidance:** read the current file before editing. If `patch_file` reports zero or multiple matches, do not repeat the same patch. Re-read the relevant lines and use `replace_lines` with the current line numbers.

## Git inspection

| Tool | Parameters | Purpose |
|---|---|---|
| `git_status` | none | Show current branch and short working-tree status. |
| `git_diff` | none | Show unstaged changes. |

These tools do not commit, push or rewrite Git history.

## Controlled OS command execution

| Tool | Parameters | Purpose |
|---|---|---|
| `os_command` | `executable`, `args`, optional `cwd`, optional `timeout_seconds` | Run one allowlisted OS command using an argument vector and the bounded process runner. |

`os_command` is available only in Agent/code mode. The current profiles permit
read-only Git inspection (`status`, summary/path-scoped `diff`, `log`,
`branch --show-current`, `rev-parse`, metadata-only `show`) and Python verification modules (`pytest`, `unittest`,
`compileall`). Arbitrary executables, scripts, Git mutations, network commands,
privileged operations, and shell syntax are rejected. Python test execution
requires approval by default because project tests can execute arbitrary code.

The tool uses `shell=False`, a project-contained working directory, argument
validation, a minimal child environment, timeout and output limits, and
process-group cleanup where supported. This is not a complete OS sandbox and
does not enforce network isolation for Python test code. See
[os_command implementation and configuration](OS_COMMAND.md).

## Verification and debugging

| Tool | Parameters | Purpose |
|---|---|---|
| `run_checks` | `check`, `path` | Run `pytest`, `unittest` or `compileall` with configured timeout/output bounds. |
| `verify_changes` | `path`, `test_check` | Run Python compilation and optionally `unittest` or `pytest` as a combined verification pass. |
| `inspect_test_failure` | `output` | Extract likely failing tests, assertions, exception types and traceback context from supplied output. It does not run tests. |

The existing `run_checks` and `verify_changes` tools require approval unless
the `execution` category or a specific tool override permits them. `os_command`
uses its per-profile approval setting; Git inspection profiles are read-only,
while Python test execution requires approval by default. All process execution
is bounded by configured timeout and output limits.

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

## Tool module organization

Shared read-only tools (project listing/reading/search, Git inspection, AST/code navigation, project/test maps and test-failure inspection) live in `modules/tools/` and are exported through its package facade. Agent-only `os_command` lives in `modules/tools/os_command.py`. The coding agent keeps write, commit, push, test execution, task-plan and `os_command` actions in `modules/engine/agent_runtime.py`, where approval, backups, timeouts and execution safeguards are enforced.

## Configuration

`assets/shai_config.json` controls core provider/model selection, approval behaviour, safeguards, test execution, logging, history limits and display settings. Provider-specific endpoint/auth overrides are in `assets/provider_config.json`; command execution and Git policy are in `assets/command_config.json`. API keys belong in environment variables, not this file.

## Context meter

Before each request, Agent and Chat display an approximate prompt-token count. The estimate is also shown in the thinking indicator. After the response, Shai displays exact prompt/completion/total token counts when the provider returns usage metadata.

A context percentage is shown only when a context limit is known. Shai captures model context metadata from the provider's model catalogue when available and stores it in `assets/shai_config.json`. If the provider does not publish a limit, the display explicitly says the limit is unknown rather than inventing a percentage. A user may add `context_window_tokens` for the currently selected model or an entry in `model_context_windows` keyed as `provider::model`.

The pre-request estimate is heuristic and should not be treated as tokenizer-exact. Debug logs record both estimates and provider-reported usage.

## Live tool activity display

Tool activity is shown on a transient terminal status line. Consecutive calls
rewrite the same line in place; repeated calls to the same tool are summarized
with a call count. Tool errors are buffered until the batch line is closed, so
they cannot collide with the status text. Interactive prompts and file previews
suspend the transient line before writing ordinary terminal output. Normal
assistant messages continue to scroll naturally.


## Granular approval configuration

```json
{
  "approve": {
    "all": false,
    "file_edit": false,
    "file_delete": false,
    "file_truncate": false,
    "execution": false,
    "git_commit": false,
    "git_push": false,
    "tools": {
      "run_checks": true
    }
  }
}
```

Approval precedence is an explicitly configured `approve.tools.<tool>` entry
first, then an explicitly configured action category, then `approve.all`, then
an interactive prompt. Add only the tool overrides you actually want; an
explicit per-tool or category `false` overrides a broader `true`. For example,
`"tools": {"run_checks": true}` approves only `run_checks` even if the broader
`execution` category is false. High-risk truncation/deletion
ignores `approve.all`; it requires an explicit category/tool `true` or a
one-time interactive confirmation. The legacy `yes_to_all` key is read for
backward compatibility and is migrated to `approve.all` when saved.

`file_delete` and Git approval categories are reserved for the corresponding
operations as they are introduced or wired into commands. Current tools do not
expose a general-purpose delete-file or arbitrary shell command.


## Project path permissions

All project file tools use generic rules in
`assets/project_access_config.json` and user-approved grants in
`.shai/project_access.json`. `view` controls reads, listings, searches and
inspection; `edit` controls ordinary modifications (existing files also need
`view` for safe diff previews); `truncate` separately gates high-risk reductions; `execute` gates controlled command working
directories and Python targets. `delete` is reserved for future deletion
tools. Interactive blocks ask for the exact file/folder, parent, or level-2
parent scope and read/write/delete/exec/all capabilities. Denied entries are
filtered from directory listings and recursive discovery. See
`docs/CONFIGURATION.md` for the full flow.
