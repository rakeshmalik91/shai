# Future Tools Roadmap

This roadmap separates delivered tools from future ideas. Implemented tools are listed in [TOOLS.md](../TOOLS.md).

## Delivered and available now

- `get_file_outline`
- `find_symbol`
- `find_references`
- `verify_changes`
- `inspect_test_failure`
- `get_project_map`
- `get_test_map`
- `update_task_plan`
- `replace_lines` for robust line-range edits

The eight priority tools and `replace_lines` are registered in the model-facing tool schema and local dispatch table. Their parameters and limitations are documented in [Existing Tools](../TOOLS.md).

## Next candidates

1. **`search_logs`** — search Shai and project logs by term, level and time range.
2. **`check_environment`** — report Python version, available executables and selected installed packages without exposing environment variables.
3. **`get_import_graph`** — summarize Python module import relationships.
4. **`git_log` and `git_blame`** — inspect commit and line history without modifying Git state.
5. **`compare_files`** — compare two files or revisions.
6. **`apply_patch`** — accept a structured patch with context and report precise mismatch locations.
7. **Persistent task plans** — optionally save task state in the project so a task can resume after restarting Shai.
8. **Semantic references** — optionally integrate language-server support where practical, while keeping the standard-library text-search fallback.

## Design principles

- Prefer Python standard-library implementations for iSH compatibility.
- Keep tools narrow, predictable and documented with explicit parameter schemas.
- Bound output and execution time.
- Preserve project-root boundaries, previews, backups and approval controls.
- Treat approximate code/test mappings as hints, not authoritative results.
- Detect repeated calls that return the same error or no new information, without blocking legitimate calls with different parameters.
- Keep user-visible action/status messages and diagnostic logs useful.
- Report verification only when the checks actually ran.
