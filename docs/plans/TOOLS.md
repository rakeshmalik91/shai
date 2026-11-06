# Future Tools Roadmap & Technical Specifications

Implemented tool contracts, parameter schemas, and delivered capabilities are documented in
[`../TOOLS.md`](../TOOLS.md). This document contains detailed engineering specifications,
parameter schemas, and security boundaries for candidate tools planned for future releases.

---

## Candidate Tools Inventory

### 1. `search_logs`

Search ShAI run logs and project logs by keyword, log level, and timestamp range.

- **Purpose:** Allow the agent to diagnose previous session errors, tracebacks, and tool execution failures without requiring direct filesystem search across raw log files.
- **Parameters Schema:**
  ```json
  {
    "type": "object",
    "properties": {
      "query": {"type": "string", "description": "Keyword, exception name, or pattern to match."},
      "level": {"type": "string", "enum": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], "description": "Minimum log level to include."},
      "max_entries": {"type": "integer", "default": 20, "description": "Maximum log entries to return (capped at 50)."},
      "scope": {"type": "string", "enum": ["current_run", "all_runs"], "default": "current_run", "description": "Whether to inspect only current session logs or historical runs in .shai/logs/."}
    },
    "required": ["query"]
  }
  ```
- **Security & Access Policy:**
  - Enforces `require_access(".shai/logs", "view")`.
  - Sensitive payload fields (API keys, Authorization headers, user tokens) are automatically redacted in returned log excerpts.
  - Head/tail bounding ensures log search results do not exceed `max_tool_result_chars`.

---

### 2. `check_environment`

Report runtime execution details, Python interpreter capabilities, and available system executables.

- **Purpose:** Let the agent discover what tools, compilers, and libraries are available in the current environment before deciding which verification or build commands to invoke.
- **Parameters Schema:**
  ```json
  {
    "type": "object",
    "properties": {
      "inspect_python": {"type": "boolean", "default": true, "description": "Report Python version, implementation (e.g. CPython), and platform."},
      "check_executables": {"type": "array", "items": {"type": "string"}, "description": "List of executable names to probe (must be in safe allowlist: ['git', 'python3', 'pytest', 'pip', 'cargo', 'node', 'make'])."}
    }
  }
  ```
- **Security Boundary:**
  - Arbitrary executable paths and environment variable inspection (`os.environ`) are strictly forbidden to prevent credential leakage.
  - Probes use `shutil.which()` against standard system PATH entries without spawning child processes.

---

### 3. `get_import_graph`

Analyze Python module dependencies, import hierarchies, and circular dependency risks across the project.

- **Purpose:** Provide high-level structural intelligence so the model can safely plan refactoring or module extraction without introducing circular import cycles.
- **Parameters Schema:**
  ```json
  {
    "type": "object",
    "properties": {
      "target_module": {"type": "string", "description": "Optional specific Python file or module to trace. If omitted, summarizes all project modules."},
      "include_external": {"type": "boolean", "default": false, "description": "Whether to include third-party package dependencies in the report."}
    }
  }
  ```
- **Return Contract:**
  - Returns a categorized summary: Internal Module Graph, Detected Cycles (if any), External Dependencies, and Standard Library imports.
  - Implemented strictly via standard library `ast` parsing; never executes or imports target modules.

---

### 4. `git_log` & `git_blame`

Inspect commit history and line-by-line attribution safely without invoking `os_command`.

- **Purpose:** Give the agent visibility into who modified specific lines, why changes were made, and recent commit messages without requiring elevated command execution privileges.
- **`git_log` Schema:**
  ```json
  {
    "type": "object",
    "properties": {
      "max_count": {"type": "integer", "default": 10, "maximum": 50, "description": "Number of recent commits to inspect."},
      "path": {"type": "string", "description": "Optional project-relative path to restrict history to."}
    }
  }
  ```
- **`git_blame` Schema:**
  ```json
  {
    "type": "object",
    "properties": {
      "path": {"type": "string", "description": "Project-relative file path to blame."},
      "start_line": {"type": "integer", "minimum": 1, "description": "1-based starting line number."},
      "end_line": {"type": "integer", "minimum": 1, "description": "1-based ending line number (span capped at 100 lines)."}
    },
    "required": ["path", "start_line", "end_line"]
  }
  ```
- **Security Boundary:**
  - Read-only Git commands invoked via argument vectors with `shell=False`.
  - Requires `view` access on the target path; path traversal (`..`) is rejected.

---

### 5. `compare_files`

Compute a bounded diff comparing two project files or comparing a working file against a Git revision.

- **Purpose:** Allow the agent to verify differences between files, review backups against current state, or compare staging vs working tree without shell access.
- **Parameters Schema:**
  ```json
  {
    "type": "object",
    "properties": {
      "file_a": {"type": "string", "description": "First file path."},
      "file_b": {"type": "string", "description": "Second file path (or revision reference)."},
      "context_lines": {"type": "integer", "default": 3, "description": "Diff context lines (capped at 10)."}
    },
    "required": ["file_a", "file_b"]
  }
  ```
- **Security Boundary:**
  - Both file paths must satisfy project path access policy (`view` capability).
  - Uses standard library `difflib.unified_diff` with bounded character/line output.

---

### 6. `apply_patch`

Apply a multi-hunk unified diff patch with precise mismatch detection and offset tolerance.

- **Purpose:** Offer a higher-level alternative to `patch_file` and `replace_lines` for applying multi-line, multi-location changes simultaneously.
- **Parameters Schema:**
  ```json
  {
    "type": "object",
    "properties": {
      "path": {"type": "string", "description": "Target project file."},
      "patch_text": {"type": "string", "description": "Unified diff patch containing one or more hunks."},
      "fuzz_lines": {"type": "integer", "default": 0, "maximum": 2, "description": "Permitted line offset tolerance."}
    },
    "required": ["path", "patch_text"]
  }
  ```
- **Safety Policy:**
  - Requires `edit` access on target path.
  - Requires user approval per `approve.file_edit` or category rules.
  - Atomic replacement with automated backup creation under `.shai/shai_backups/`.
  - Rejects patches that do not match cleanly, reporting the exact failing hunk number and mismatched context lines.

---

### 7. Persistent Task Plans

Persist `TASK_PLAN` state to `.shai/task_plan.json` so long-running multi-turn tasks can survive session restarts.

- **Schema:**
  ```json
  {
    "version": 1,
    "task_id": "uuid-string",
    "updated_at": "ISO-timestamp",
    "steps": [
      {"id": 1, "title": "Analyze bug report", "status": "completed"},
      {"id": 2, "title": "Implement regression test", "status": "in_progress"},
      {"id": 3, "title": "Apply fix to module", "status": "pending"}
    ]
  }
  ```
- **Lifecycle & Retention:**
  - Automatically deleted or marked completed when all steps reach `completed`.
  - Prompts user upon session start if an incomplete plan from a previous session is detected.

---

### 8. Semantic Code References (Language Server Protocol)

Integrate lightweight LSP client capabilities for symbol renaming, jump-to-definition, and accurate reference analysis.

- **Design Principles:**
  - Pure standard-library fallback: When no language server is running, tools automatically fall back to AST and text-based search (`find_symbol`, `find_references`).
  - Strict performance budgets: Timeout of 5 seconds for LSP queries to maintain responsiveness on lightweight devices (e.g. iSH).

---

## Universal Design Principles for All Tools

1. **Standard Library Preference:** Maintain compatibility with resource-constrained environments (iSH, Alpine) without requiring external C extensions.
2. **Deterministic Schemas:** Every tool parameter is defined in `assets/tools_registry.json` with explicit type constraints and descriptions.
3. **Bounded Execution & Output:** All tools bound output via `max_tool_result_chars` and retain head/tail context when truncated.
4. **Access Control Integration:** Every tool resolves canonical project paths and invokes `require_access(path, capability)`.
5. **No Blind Retries:** When a tool fails, clear diagnostic hints (such as switching from exact patch to line replacement) are returned to prevent duplicate failing calls.
