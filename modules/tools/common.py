"""Shared project-root state and path safety for project tools."""
import pathlib

ROOT = pathlib.Path.cwd().resolve()
MAX_FILE_SIZE = 200_000

def configure_root(path):
    global ROOT
    ROOT = pathlib.Path(path).expanduser().resolve()
    return ROOT

def safe_path(path):
    target = (ROOT / path).resolve()
    try:
        target.relative_to(ROOT)
    except ValueError:
        raise ValueError("Path escapes project root.")
    return target
