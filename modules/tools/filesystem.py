"""Filesystem-oriented project inspection tools with centralized access checks."""
import pathlib
from . import common
from .common import safe_path, require_access, ProjectAccessDenied


def _allowed(path, permission="view"):
    try:
        require_access(path, permission)
        return True
    except (ProjectAccessDenied, ValueError):
        return False


def list_files(path="."):
    """List only entries the active project policy permits the agent to view."""
    try:
        target = safe_path(path)
        require_access(path, "view")
    except (ValueError, ProjectAccessDenied) as error:
        return "Access denied: {}".format(error)
    if not target.is_dir():
        return "Not a directory."
    entries = []
    for entry in sorted(target.iterdir()):
        try:
            relative = entry.relative_to(common.ROOT).as_posix()
            require_access(relative, "view")
            # Do not expose symlink targets outside the project.
            entry.resolve().relative_to(common.ROOT)
        except (ValueError, ProjectAccessDenied):
            continue
        entries.append(relative + ("/" if entry.is_dir() else ""))
    return "\\n".join(entries) if entries else "(empty)"


def read_file(path, offset=0, limit=100):
    """Read a project file in line-based chunks using path, offset, and limit."""
    try:
        target = safe_path(path)
        require_access(path, "view")
    except (ValueError, ProjectAccessDenied) as error:
        return "Access denied: {}".format(error)
    if not target.is_file():
        return "File does not exist."
    try:
        size = target.stat().st_size
    except OSError:
        return "Unable to read file."
    if size > common.MAX_FILE_SIZE:
        return "File is too large."
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "File is not UTF-8 text."
    except OSError as error:
        return "Unable to read file: " + str(error)
    lines = text.splitlines()
    total = len(lines)
    if offset < 0 or offset >= total:
        return "Invalid offset: {} (file has {} lines).".format(offset, total)
    if limit <= 0:
        return "Invalid limit: must be positive."
    end = min(offset + limit, total)
    return "Lines {}-{} of {}:\\n{}".format(offset + 1, end, total, "\\n".join(lines[offset:end]))


def search_files(query, path="."):
    """Search visible files only, pruning denied directories before recursion."""
    try:
        target = safe_path(path)
        require_access(path, "view")
    except (ValueError, ProjectAccessDenied) as error:
        return "Access denied: {}".format(error)
    if not target.exists():
        return "Path does not exist."
    results = []
    if target.is_file():
        files = [target] if _allowed(target.relative_to(common.ROOT).as_posix()) else []
    else:
        files = []
        for directory, dirnames, filenames in __import__("os").walk(str(target), followlinks=False):
            current = pathlib.Path(directory)
            kept = []
            for name in dirnames:
                child = current / name
                rel = child.relative_to(common.ROOT).as_posix()
                if _allowed(rel) and not child.is_symlink():
                    kept.append(name)
            dirnames[:] = kept
            for name in filenames:
                child = current / name
                if _allowed(child.relative_to(common.ROOT).as_posix()):
                    files.append(child)
    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if str(query).lower() in line.lower():
                results.append("{}:{}: {}".format(file_path.relative_to(common.ROOT), i, line.strip()))
                if len(results) >= 100:
                    return "\\n".join(results) + "\\n... (truncated at 100 results)"
    return "\\n".join(results) if results else "No matches found."
