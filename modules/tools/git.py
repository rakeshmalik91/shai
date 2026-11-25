"""Git inspection commands exposed as read-only tools."""
import os
import subprocess
from . import common


def _no_window_kwargs():
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def git_status():
    """Show current Git status."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=common.ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            **_no_window_kwargs(),
        )
        if result.returncode != 0:
            return "Git status failed: " + result.stderr.strip()
        return result.stdout.strip() or "(clean)"
    except (OSError, subprocess.TimeoutExpired) as error:
        return "Git status error: " + str(error)


def git_diff():
    """Inspect Git changes."""
    try:
        result = subprocess.run(
            ["git", "diff"],
            cwd=common.ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            **_no_window_kwargs(),
        )
        if result.returncode != 0:
            return "Git diff failed: " + result.stderr.strip()
        return result.stdout.strip() or "(no changes)"
    except (OSError, subprocess.TimeoutExpired) as error:
        return "Git diff error: " + str(error)


