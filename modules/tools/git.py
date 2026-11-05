"""Git inspection commands exposed as read-only tools."""
import subprocess
from . import common
def git_status():
    """Show current Git status."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=common.ROOT,
            capture_output=True,
            text=True,
            timeout=10,
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
        )
        if result.returncode != 0:
            return "Git diff failed: " + result.stderr.strip()
        return result.stdout.strip() or "(no changes)"
    except (OSError, subprocess.TimeoutExpired) as error:
        return "Git diff error: " + str(error)


