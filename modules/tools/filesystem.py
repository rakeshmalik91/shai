"""Filesystem-oriented project inspection tools."""
import pathlib
from . import common
from .common import safe_path
def list_files(path):
    """List project files under a directory."""
    target = safe_path(path)
    if not target.is_dir():
        return "Not a directory."
    entries = []
    for entry in sorted(target.iterdir()):
        rel = entry.relative_to(common.ROOT)
        if entry.is_dir():
            entries.append(str(rel) + "/")
        else:
            entries.append(str(rel))
    return "\n".join(entries) if entries else "(empty)"


def read_file(path, offset=0, limit=100):
    """Read a project file in line-based chunks using path, offset, and limit."""
    target = safe_path(path)
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
    chunk = lines[offset:end]
    header = "Lines {}-{} of {}:".format(offset + 1, end, total)
    return header + "\n" + "\n".join(chunk)


def search_files(query, path="."):
    """Search a project file or directory."""
    target = safe_path(path)
    if not target.exists():
        return "Path does not exist."
    results = []
    if target.is_file():
        files = [target]
    else:
        files = [f for f in target.rglob("*") if f.is_file()]
    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if query.lower() in line.lower():
                rel = file_path.relative_to(common.ROOT)
                results.append("{}:{}: {}".format(rel, i, line.strip()))
                if len(results) >= 100:
                    return "\n".join(results) + "\n... (truncated at 100 results)"
    return "\n".join(results) if results else "No matches found."


