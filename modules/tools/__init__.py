"""Project tools split by responsibility, with a stable public facade."""
from .common import (
    configure_root, configure_access_config, safe_path, require_access,
    access_decision, load_access_policy, ProjectAccessDenied, ROOT, MAX_FILE_SIZE
)
from .filesystem import list_files, read_file, search_files
from .git import git_status, git_diff
from .inspection import (
    get_file_outline, find_symbol, find_references, get_project_map,
    get_test_map, inspect_test_failure,
)
from .schemas import TOOL_DISPATCH_READ_ONLY, FUNCTIONS_READ_ONLY
