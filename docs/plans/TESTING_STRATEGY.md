# Testing Strategy & Test Hardening Roadmap

## 1. Overview & Objective

This document defines the quality assurance architecture and test hardening roadmap for ShAI.
The goal is to maintain a robust, deterministic, regression-free test suite that executes
reliably using only the Python standard library while supporting optional `pytest` execution
and automated syntax compilation checks.

---

## 2. Core Testing Principles

1. **Zero External Dependencies Required:**
   The primary test suite must execute completely using Python's built-in `unittest` module.
   Environments without network access or third-party packages (such as minimal containers or iSH)
   must achieve 100% test pass rates without relying on `pytest` or `mock` libraries outside `unittest.mock`.

2. **Hermetic & Isolated Execution:**
   - Every test that touches files, directories, or configurations must execute in an isolated `tempfile.TemporaryDirectory()`.
   - Tests must never mutate the host repository, write to `.shai/` in the project root, or modify environment variables without guaranteed cleanup via `tearDown()` or context managers.
   - Test execution must never block on user input or read from `sys.stdin`.

3. **Multi-Stream Durable Reporting:**
   Every test run generates structured, timestamped artifacts under `test/`:
   - `test_report.txt`: Human-readable summary with detailed failure tracebacks.
   - `test_report.json`: Machine-readable summary with exact timings and test details.
   - `test_report_pytest.xml`: JUnit XML report (generated when pytest is installed).

4. **Negative & Edge-Case Coverage Floor:**
   Happy-path testing is insufficient. Every module must explicitly test:
   - Malformed, corrupt, or truncated JSON files.
   - Invalid parameter types (booleans passed where ints/strings expected, None, floats).
   - Boundary limits, overflows, negative values, and zero values.
   - Path traversal (`../`), absolute paths, and symlink escapes.
   - Non-UTF8 binary data and Unicode character sets.

---

## 3. Test Architecture & Coverage Matrix

| Test Suite | Module Under Test | Hardening Focus Areas |
|---|---|---|
| `test_configuration.py` | `modules.engine.configuration` | Configuration defaults, clamping bounds, boolean normalization, granular approval precedence, retry HTTP status filtering. |
| `test_tool_registry.py` | `modules.tools.registry`, `schemas` | Schema validation, duplicate name detection, required parameter verification, chat availability segregation. |
| `test_tools_module.py` | `modules.tools.filesystem`, `common` | Directory traversal rejection, empty directory handling, binary file rejection, offset/limit bounds, search capping. |
| `test_priority_tools.py` | `modules.tools.inspection`, `file_editing` | Line replacement boundary checks, AST syntax error resilience, symbol lookup in async/class contexts, test failure regex extraction. |
| `test_file_change_display.py`| `modules.file_change_display` | Large diff truncation, head/tail preservation, setting load fallbacks, disabled previews. |
| `test_conversation_history.py`| `modules.conversation_history` | Corrupt history recovery, atomic save operations, deep-copy message isolation, input history deduplication and capping. |
| `test_context_meter.py` | `modules.context_meter` | Multi-byte Unicode estimation, override precedence, missing usage recovery, status formatting. |
| `test_model_routing.py` | `modules.model_routing` | Template merging, project overrides, priority tie-breaking, invalid candidate skipping, availability cache invalidation. |
| `test_shai_command.py` | `modules.commands.shai_command` | Politeness prefix/suffix stripping, case-insensitivity, mode restrictions, disabled routing flag. |
| `test_backup_management.py` | `modules.engine.configuration` | Backup directory migration, retention cleanup policies, new file creation handling. |
| `test_os_command.py` | `modules.tools.os_command` | Argument allowlists, shell syntax prevention, pathspec validation, timeout ceilings. |
| `test_project_access_control.py`| `modules.tools.common` | Glob pattern matching, deny-overrides-allow precedence, interactive grant persistence. |
| `test_bounded_execution.py`| `modules.engine.process_control` | Stdout/stderr byte bounds, head/tail preservation, timeout enforcement, process group cleanup. |
| `test_agent_safeguards.py` | `modules.agent_safeguards` | Compaction of tool results, repeat failure limits, argument schema validation. |
| `test_invalid_tool_recovery.py`| `modules.engine.history_recovery` | Provider tool history repair, thinking block sanitization, HTTP 400 rejection. |

---

## 4. Verification Workflow

Run the test suite via the standard runner:

```sh
sh run_all_tests.sh
```

Or invoke the Python runner directly:

```sh
python3 test/run_tests.py
```

The runner performs:
1. `unittest` discovery across all `test/test_*.py` suites.
2. Optional `pytest` execution if available.
3. Syntax compilation of all Python files using `compileall`.
4. Structured report generation in `test/test_report.txt` and `test/test_report.json`.
