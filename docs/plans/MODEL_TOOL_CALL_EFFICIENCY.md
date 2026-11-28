# Model Tool-Call Efficiency Plan

**Status:** Planned  
**Scope:** Reduce the number of model round-trips required to inspect, understand, modify, and verify a project without weakening access controls, approval gates, or output limits.

This plan is based on an audit of the current tool registry, runtime dispatch, tool implementations, and automated tests. It proposes changes; it does not claim that the optimizations below are already implemented.

## 1. Goals and non-goals

### Goals

- Reduce sequential model-to-tool round-trips for common coding tasks.
- Reduce repeated reads, searches, and directory scans.
- Return enough context in each result for the model to choose the next action correctly.
- Make tool descriptions and schemas easier for models to follow on the first attempt.
- Preserve precise file paths, line numbers, diagnostics, and safe-edit workflows.
- Measure improvement using tool-call count, completion rate, latency, and token usage.

### Non-goals

- Do not replace model reasoning with opaque automation.
- Do not combine unrelated operations into one large tool.
- Do not bypass path access checks, approval, backups, command allowlists, or execution limits.
- Do not return entire projects or unbounded file contents merely to save a call.
- Do not silently mutate files as a side effect of inspection tools.

## 2. Current tool inventory and audit

The canonical declarations are in `assets/tools_registry.json`. Agent declarations are loaded from that registry and dispatched by `modules/engine/agent_runtime.py`. Chat exposes the registry entries marked `chat_available` and uses the read-only dispatch facade in `modules/tools/schemas.py`.

| Tool | Current role | Availability | Efficiency observations |
|---|---|---|---|
| `list_files` | List one directory | Agent + Chat | A tree/depth option could avoid repeated directory listing. |
| `read_file` | Read one line-bounded file segment | Agent + Chat | Multi-file reads and targeted symbol/context reads could reduce sequential reads. |
| `search_files` | Case-insensitive literal search in one path | Agent + Chat | One query per call; no structured match cap/context options in the public schema. |
| `git_status` | Working-tree status | Agent + Chat | Often paired with `git_diff`; a combined inspection option may save a round-trip. |
| `git_diff` | Unstaged diff | Agent + Chat | No path/summary options in the current public schema. |
| `edit_file` | Compatibility entry for file replacement | Agent only | Normalized to the targeted patch workflow; legacy surface may confuse model selection. |
| `patch_file` | Exact unique-block replacement | Agent only | Good for narrow edits; mismatch recovery can require reread + another edit call. |
| `replace_lines` | Replace a line range | Agent only | Useful fallback, but often requires a separate read/outline call to obtain line numbers. |
| `run_checks` | Run one configured Python check | Agent only | Overlaps with `verify_changes`; separate checks can require multiple calls. |
| `get_file_outline` | AST outline of one Python file | Agent + Chat | Often followed by `read_file`; consider optional symbol/source excerpts. |
| `find_symbol` | Find exact Python definitions | Agent + Chat | Separate from `find_references`; both may be needed for a change. |
| `find_references` | Textual Python reference search | Agent + Chat | Not semantic; can be combined with definition lookup. |
| `verify_changes` | Compile and optionally run a test suite | Agent only | Consolidates compile + one test runner; overlaps with `run_checks`. |
| `inspect_test_failure` | Parse supplied test output | Agent + Chat | Requires a preceding test execution and a second model tool call. |
| `get_project_map` | Summarize project shape and likely entry points | Agent + Chat | Performs a project walk; may duplicate `list_files` / `get_test_map`. |
| `get_test_map` | List test files | Agent + Chat | Separate project scan; useful as a component of a unified project overview. |
| `update_task_plan` | Update visible task checklist | Agent only | Planning state operation; should remain distinct from code inspection. |
| `os_command` | Bounded allowlisted OS command | Agent only | Broad fallback; should not be the default when a dedicated tool exists. |

### Runtime and contract checks

- Registry contains **18 unique tool declarations**.
- Every declared tool has an Agent dispatch handler.
- No Agent dispatch handler is absent from the canonical registry.
- Every registry tool marked `chat_available` is present in the Chat read-only dispatch table.
- Tool declaration and implementation boundaries are split across `assets/tools_registry.json`, `modules/tools/`, and `modules/engine/agent_runtime.py`; tests should continue to enforce this consistency.
- The existing test suite passed during this audit: **346 tests and 74 subtests**. A spreadsheet-runtime warmup warning was emitted by the surrounding Python environment after pytest completed; it did not change pytest's successful exit status.

## 3. Main sources of avoidable tool calls

### 3.1 One-file-at-a-time inspection

A typical change can require:
1. `get_file_outline`
2. `read_file`
3. `find_symbol` or `search_files`
4. `find_references`
5. Another `read_file` for a second file

These are individually useful primitives, but the model must orchestrate them serially and repeatedly provide paths and symbols.

### 3.2 Duplicate project scans

`list_files`, `get_project_map`, and `get_test_map` independently inspect project structure. Models may call two or three of them to understand a repository.

### 3.3 Separate test execution and failure interpretation

The model runs `run_checks` or `verify_changes`, receives a large output, then calls `inspect_test_failure` with that output. A structured verification result can include a bounded failure summary directly.

### 3.4 Similar Git inspection calls

The common “what changed?” workflow frequently needs both status and diff. The current no-argument tools force two round-trips and do not allow a path-scoped or summary-first diff.

### 3.5 Schema and instruction ambiguity

Some descriptions are long and encode workflow rules in prose. Long descriptions consume context and can still be misinterpreted. Other useful options (batching, result limits, context lines) are not represented in the current schemas.

### 3.6 Recovery after an imperfect call

A wrong path, invalid range, missing parameter, or exact-patch mismatch can require another read and another tool call. Existing argument validation and repeat detection help prevent loops, but results can give more actionable next-step information.

## 4. Proposed improvements

Implement in small, measurable phases. Prefer extending stable tools where the behavior remains cohesive; introduce a new tool only when it clearly removes multiple calls without making the schema unwieldy.

### Phase 1 — Improve existing tool results and descriptions

**Priority: P0; low risk**

1. **`read_file`**
   - Return explicit `next_offset`, `total_lines`, and `has_more` metadata in a structured result.
   - Support a bounded `around_line` or `symbol` mode so a model can fetch the relevant implementation without first calling `get_file_outline`.
   - Preserve the existing line-range mode for compatibility.
   - Include a compact file header (path, total lines, encoding) with each response.
2. **`search_files`**
   - Add `max_results`, `context_lines`, and optional glob filters.
   - Return structured matches with path, line, and excerpt fields.
   - Allow a small bounded list of literal queries in one call, preserving independent result groups.
3. **`get_file_outline`**
   - Optionally include signatures, decorators, and source line ranges for selected symbols.
   - Add a `symbols` filter so one call can locate and outline requested definitions.
4. **Tool descriptions**
   - Make descriptions concise and action-oriented.
   - State the preferred workflow and when *not* to call a tool.
   - Remove duplicated policy prose where the system prompt already defines it.
   - Explicitly document defaults and limits in JSON schema fields.

**Acceptance:** common inspect-and-read tasks require fewer calls, while all existing argument and access-control tests remain green.

### Phase 2 — Add bounded composite inspection tools

**Priority: P1; medium risk**

1. **`inspect_project`**
   - One call returns a bounded directory tree, file counts/types, likely entry points, test files, and key project metadata.
   - Parameters: `path`, `max_depth`, `max_entries`, and optional `include_tests`.
   - Avoid repeating a filesystem walk for project map and test map.
2. **`inspect_code`**
   - One call accepts one or more paths and optional symbol names.
   - Returns AST outlines, selected source excerpts, definition locations, and textual references within the requested scope.
   - Must label textual references as heuristic, not semantic.
   - Must not read denied paths or follow symlinks outside the project.
3. **`read_files`**
   - Read a bounded list of files/ranges in one call.
   - Each file result has independent success/error metadata; one inaccessible file must not hide other results.
   - Enforce aggregate byte/character limits and per-file limits.

Do not make these tools call the model or recursively invoke other tools. They should be deterministic local operations over one request.

**Acceptance:** a representative “understand this module and its tests” task should need one composite inspection call plus, at most, one focused read call.

### Phase 3 — Consolidate verification and Git inspection

**Priority: P1; medium risk**

1. **Verification**
   - Extend `verify_changes` to support an ordered bounded list of checks (compile, tests, lint/type checks only when configured).
   - Return structured fields: command/check, exit code, duration, pass/fail, bounded stdout/stderr, parsed failing test IDs, and likely traceback summary.
   - Keep `inspect_test_failure` available for pasted logs and cases where the built-in parser is insufficient.
   - Clarify whether `run_checks` remains a low-level single-check tool or becomes a compatibility alias. Do not silently remove it.
2. **Git inspection**
   - Add a `git_inspect` read-only tool that can return status plus a bounded diff summary and optional path-scoped diff in one invocation.
   - Keep existing `git_status` and `git_diff` as compatibility tools initially.
   - Ensure metadata-only revision inspection and existing read-only command policy are preserved.

**Acceptance:** the common verify-after-edit and inspect-working-tree flows each complete with one tool call, without weakening execution approval.

### Phase 4 — Reduce orchestration and recovery overhead

**Priority: P2; medium/high risk**

1. Add deterministic **tool-call planning hints** to the Agent system instructions:
   - Use one project overview instead of separately listing files and tests.
   - Batch independent reads/searches.
   - Prefer `inspect_code` for symbol-oriented exploration.
   - Run verification once after the coherent edit batch, not after every file.
   - Do not request information already present in tool results or conversation history.
2. Add bounded **read-result caching** within a request:
   - Cache by canonical path + range/query + file modification fingerprint.
   - Invalidate after edits and Git operations that change the working tree.
   - Never reuse cached results across projects or permission changes.
3. Improve actionable error results:
   - For invalid line ranges, return the valid line count and nearest valid range.
   - For patch mismatch, return a compact mismatch excerpt and recommend `replace_lines` only when appropriate.
   - For no search matches, return the searched scope and filters so the model can broaden deliberately.
4. Track tool-call counts by category and reason (inspection, edit, Git, verification, recovery), without logging secrets or full sensitive file contents.

**Acceptance:** fewer repeated calls and fewer correction calls in multi-file tasks, with no increase in unsafe actions or failed task completion.

## 5. Tool design principles

- **Cohesive batching:** Batch independent operations of the same kind; avoid a generic “do everything” tool.
- **Bounded output:** Every composite tool must enforce both per-item and aggregate output limits.
- **Structured results:** Prefer machine-readable status, metadata, and arrays over prose-only output where it improves next-step selection.
- **Access control first:** Resolve canonical paths and enforce `require_access` for every requested item, including each item in a batch.
- **Partial success:** Report per-item errors and continue safe independent inspection; never silently omit failures.
- **No hidden side effects:** Inspection tools remain read-only. Edits and execution retain their existing approval flows.
- **Compatibility:** Keep current tools available while new tools are tested; deprecate only with migration notes and tests.
- **Resource constraints:** Use the standard library where practical and keep algorithms suitable for iSH and low-resource environments.

## 6. Measurement plan

Establish a baseline before implementation using a fixed set of representative tasks:

1. Locate a function and all call sites.
2. Understand a project and identify its tests.
3. Diagnose a failing test from a fresh test run.
4. Review current working-tree changes.
5. Make a small multi-file change and verify it.

Record per task:

- Model tool-call count (including failed/recovery calls).
- Number of sequential tool round-trips.
- Total tool output characters/tokens.
- End-to-end completion time.
- First-pass success rate (valid tool name and arguments on first attempt).
- Task completion and regression-test outcome.

Compare baseline and post-change runs with the same model, prompt, project snapshot, and task. Report medians across repeated runs. Suggested initial targets:

- 25–40% fewer tool calls on inspection-heavy tasks.
- 20% fewer sequential round-trips.
- No regression in task completion or test pass rate.
- No increase in access-control or approval-policy violations.
- No unbounded growth in tool output or prompt tokens.

These are targets, not measured results.

## 7. Test requirements

For every new or extended tool, add tests for:

- Registry schema, required fields, defaults, limits, and dispatch coverage.
- Agent and Chat availability flags.
- Per-path access checks in multi-path requests.
- Symlinks, traversal attempts, denied directories, Unicode, and unreadable files.
- Partial success and per-item error reporting.
- Aggregate output caps and deterministic truncation metadata.
- Cache invalidation after edits and isolation between projects.
- Stable ordering and deterministic output.
- Approval behavior unchanged for execution and modifications.
- Backward compatibility for existing tool names and parameter forms.

Add integration tests that assert tool-call counts for the five benchmark tasks in Section 6. Unit tests alone cannot prove that the model needs fewer round-trips.

## 8. Recommended implementation order

1. Instrument baseline tool-call counts and output sizes.
2. Improve `read_file`, `search_files`, and `get_file_outline` result contracts.
3. Implement `inspect_project` and `read_files`.
4. Implement `inspect_code`.
5. Consolidate `verify_changes` and add `git_inspect`.
6. Add caching, orchestration hints, and benchmark-based tuning.
7. Consider deprecating overlapping tools only after compatibility and usage data support it.

## 9. Current limitations to keep visible

- `find_references` is textual, not a semantic reference engine.
- `get_test_map` uses filename heuristics and does not prove test-to-module coverage.
- `get_project_map` scans files to estimate project shape and likely entry points.
- `search_files` is literal, case-insensitive search and currently caps at 100 matches.
- `read_file` is line-chunked; large files can require repeated calls.
- `run_checks` and `verify_changes` overlap in purpose but expose different execution contracts.
- `git_status` and `git_diff` are separate no-argument operations.
- `os_command` is a broad fallback with stronger policy and approval considerations; dedicated tools should be preferred when available.

---

**Decision requested for implementation:** begin with Phase 1 and baseline instrumentation, then use benchmark results to decide which composite tools in Phase 2 provide the largest real reduction.
