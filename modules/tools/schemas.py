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
FUNCTIONS_READ_ONLY = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List project files under a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path to list"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a project file in line-based chunks using path, offset, and limit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"},
                    "offset": {"type": "integer", "description": "Zero-based line offset", "default": 0},
                    "limit": {"type": "integer", "description": "Number of lines to read", "default": 100}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search a project file or directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "path": {"type": "string", "description": "File or directory to search", "default": "."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show current Git status.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Inspect Git changes.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_outline",
            "description": "Return top-level imports, classes, functions and methods with line ranges.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Python file path"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_symbol",
            "description": "Find Python definitions of a class, function, method or assignment by exact name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Symbol name to find"},
                    "path": {"type": "string", "description": "File or directory to search", "default": "."}
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_references",
            "description": "Search project text files for usages of a symbol.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Symbol name to find references for"},
                    "path": {"type": "string", "description": "File or directory to search", "default": "."}
                },
                "required": ["symbol"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_map",
            "description": "Summarize project directories, file types and likely Python entry points.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Project directory path", "default": "."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_test_map",
            "description": "List test files and heuristically map their names to likely source modules.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Project directory path", "default": "."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_test_failure",
            "description": "Extract likely failing test names, assertion errors, tracebacks and relevant context from supplied test output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "output": {"type": "string", "description": "Test output to analyze"}
                },
                "required": ["output"]
            }
        }
    },
]

