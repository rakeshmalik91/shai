"""Read-only tool schemas and dispatch table."""
from .filesystem import list_files, read_file, search_files
from .git import git_status, git_diff
from .inspection import (
    get_file_outline, find_symbol, find_references, get_project_map,
    get_test_map, inspect_test_failure,
)

TOOL_DISPATCH_READ_ONLY = {
    "list_files": list_files,
    "read_file": read_file,
    "search_files": search_files,
    "git_status": git_status,
    "git_diff": git_diff,
    "get_file_outline": get_file_outline,
    "find_symbol": find_symbol,
    "find_references": find_references,
    "get_project_map": get_project_map,
    "get_test_map": get_test_map,
    "inspect_test_failure": inspect_test_failure,
}

# OpenAI-compatible function declarations for read-only tools
# Declarations are maintained centrally in assets/tools_registry.json.
from .registry import load_tool_functions

FUNCTIONS_READ_ONLY = load_tool_functions(chat_only=True)
