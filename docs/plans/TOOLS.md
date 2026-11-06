# Future tools roadmap

Implemented tool contracts and limitations are documented in
[`../TOOLS.md`](../TOOLS.md). This file lists only candidates not currently
implemented.

## Candidate tools and capabilities

1. **`search_logs`** — search ShAI and project logs by term, level and time
   range, with bounded results and access-policy enforcement.
2. **`check_environment`** — report Python version, available executables and
   selected installed packages without exposing environment variables,
   credentials, or sensitive paths.
3. **`get_import_graph`** — summarize Python module import relationships.
4. **`git_log` and `git_blame`** — inspect commit and line history without
   modifying Git state.
5. **`compare_files`** — compare two files or revisions with bounded output.
6. **`apply_patch`** — accept a structured patch and report precise mismatch
   locations; retain previews, backups, and access checks.
7. **Persistent task plans** — optionally persist task state so a task can
   resume after restarting ShAI. Define retention and privacy behavior first.
8. **Semantic references** — optionally integrate language-server support
   while retaining the standard-library text-search fallback.

## Design principles

- Prefer Python standard-library implementations for iSH compatibility.
- Keep tools narrow, predictable and documented with explicit parameter
  schemas.
- Bound output and execution time.
- Preserve project-root boundaries, previews, backups, and approval controls.
- Treat approximate code/test mappings as hints, not authoritative results.
- Detect repeated calls that return the same error or no new information,
  without blocking legitimate calls with different parameters.
- Report verification only when checks actually ran.

## Separate future hardening plans

- [`LLM_CLARIFICATION_QUESTIONS.md`](LLM_CLARIFICATION_QUESTIONS.md) —
  suspend a task for user input and resume it with the answer.
- [`OS_COMMAND_SANDBOX.md`](OS_COMMAND_SANDBOX.md) — add OS-level isolation
  and network/resource controls for untrusted project code.
- [`EXECUTION_FLOW_ARCHITECTURE.md`](EXECUTION_FLOW_ARCHITECTURE.md) —
  refactor session lifecycle, state transitions, orchestration, and events.

Project path access control is delivered; see
[`../PROJECT_PATH_ACCESS_CONTROL.md`](../PROJECT_PATH_ACCESS_CONTROL.md).
