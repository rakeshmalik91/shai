# Future Roadmap

This is a proposal for future Shai capabilities. These items are **not represented as implemented tools** in the current codebase unless explicitly marked as existing in [TOOLS.md](TOOLS.md).

## 1. Code navigation and understanding

**Goal:** reduce broad file reads and help the agent locate the right code before editing.

- `get_file_outline` — list imports, classes, functions, and their line ranges. For Python, use the standard-library `ast` module.
- `find_symbol` — locate a class, function, method, or variable definition.
- `find_references` — find usages of a symbol across the project.
- `get_import_graph` — show module import relationships.
- `get_project_map` — produce a compact project structure and entry-point summary.
- `get_test_map` — identify tests likely related to a source module.

## 2. Verification and debugging

**Goal:** make it easier to establish whether a change worked and why a check failed.

- `verify_changes` — run a configured sequence of syntax, import, and relevant test checks after edits.
- `inspect_test_failure` — extract the failing test, assertion, traceback, and relevant output from a test run.
- `inspect_exception` — summarize an exception and its traceback.
- `search_logs` — search Shai or project logs by text, level, or time range.
- `check_environment` — report Python version, available executables, and relevant installed packages.
- `compare_files` — compare two files or revisions and summarize the differences.

## 3. Safer and more capable editing

**Goal:** make changes more targeted and easier to review.

- `apply_patch` — apply a structured patch with explicit context and a clear failure when the context does not match.
- `replace_symbol` — replace a named function or class while preserving surrounding code.
- `git_blame` — inspect line-level change history.
- `git_log` — inspect recent commits and affected files.

Any future editing tool should preserve Shai's project-root boundary, preview changes, and require approval according to configuration.

## 4. Task planning and progress

**Goal:** make long tasks understandable and prevent aimless tool-call cycles.

- Maintain a short task plan with pending, active, completed, and blocked steps.
- Record the evidence or tool result that completed each step.
- Detect repeated calls that produce the same error or no new information.
- Show concise action/status text in the interface when the model returns tool calls without accompanying assistant text.
- Require relevant verification before reporting a task as complete.

## 5. Implementation order

Suggested order:

1. `get_file_outline`
2. `find_symbol` and `find_references`
3. `verify_changes`
4. `inspect_test_failure`
5. `get_project_map` and `get_test_map`
6. Task planning and progress tracking
7. `search_logs` and `check_environment`
8. Structured patching and Git history tools

## Design principles

- Prefer Python standard-library implementations where practical, especially for iSH compatibility.
- Keep tools narrow, predictable, and easy for the model to use.
- Include explicit schemas, parameter descriptions, bounded output, and actionable errors.
- Do not make repeated-call safeguards block legitimate work with new parameters.
- Make tool activity and failures visible to the user.
- Treat verification as evidence, not as an assumption: never report tests as passing unless they actually ran successfully.
