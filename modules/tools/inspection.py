"""AST, project-map and test-output inspection tools."""
import ast
import pathlib
from . import common
from .common import safe_path
def get_file_outline(path):
    """Return top-level imports, classes, functions and methods with line ranges."""
    target = safe_path(path)
    if not target.is_file():
        return "Expected a Python source file."
    try:
        text = target.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as error:
        return "Unable to read file: " + str(error)
    if len(text) > common.MAX_FILE_SIZE:
        return "Python file exceeds maximum size."
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        return "Invalid Python syntax: " + str(error)
    lines = text.splitlines()
    outline = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                outline.append("import {} at line {}".format(alias.name, node.lineno))
        elif isinstance(node, ast.ClassDef):
            outline.append("class {} at line {}".format(node.name, node.lineno))
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    outline.append("  def {} at line {}".format(item.name, item.lineno))
        elif isinstance(node, ast.FunctionDef):
            # Only top-level functions
            if isinstance(node.parent, ast.Module) if hasattr(node, 'parent') else True:
                outline.append("def {} at line {}".format(node.name, node.lineno))
    return "\n".join(outline) if outline else "(no imports, classes, or functions found)"


def find_symbol(symbol, path="."):
    """Find Python definitions of a class, function, method or assignment by exact name."""
    if not symbol or not isinstance(symbol, str):
        return "symbol must be a non-empty string."
    target = safe_path(path)
    if not target.exists():
        return "Path does not exist."
    results = []
    files = [target] if target.is_file() else [f for f in target.rglob("*.py") if f.is_file()]
    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if len(text) > common.MAX_FILE_SIZE:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == symbol:
                    rel = file_path.relative_to(common.ROOT)
                    results.append("{} at line {} in {}".format(node.name, node.lineno, rel))
            elif isinstance(node, ast.Assign):
                for target_node in node.targets:
                    if isinstance(target_node, ast.Name) and target_node.id == symbol:
                        rel = file_path.relative_to(common.ROOT)
                        results.append("{} = ... at line {} in {}".format(symbol, node.lineno, rel))
    return "\n".join(results) if results else "No definitions found for '{}'.".format(symbol)


def find_references(symbol, path="."):
    """Search project text files for usages of a symbol."""
    if not symbol or not isinstance(symbol, str):
        return "symbol must be a non-empty string."
    target = safe_path(path)
    if not target.exists():
        return "Path does not exist."
    results = []
    files = [target] if target.is_file() else [f for f in target.rglob("*.py") if f.is_file()]
    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if symbol in line:
                rel = file_path.relative_to(common.ROOT)
                results.append("{}:{}: {}".format(rel, i, line.strip()))
                if len(results) >= 100:
                    return "\n".join(results) + "\n... (truncated at 100 results)"
    return "\n".join(results) if results else "No references found for '{}'.".format(symbol)


def get_project_map(path="."):
    """Summarize project directories, file types and likely Python entry points."""
    target = safe_path(path)
    if not target.is_dir():
        return "Project map path must be a directory."
    summary = []
    py_files = 0
    total_files = 0
    for entry in target.rglob("*"):
        if entry.is_file():
            total_files += 1
            if entry.suffix == ".py":
                py_files += 1
    summary.append("Project root: {}".format(target))
    summary.append("Total files: {}".format(total_files))
    summary.append("Python files: {}".format(py_files))
    # Find likely entry points
    entry_points = []
    for entry in target.rglob("*.py"):
        if entry.is_file():
            try:
                text = entry.read_text(encoding="utf-8")
                if "if __name__ == \"__main__\":" in text or "def main(" in text:
                    entry_points.append(str(entry.relative_to(common.ROOT)))
            except (UnicodeDecodeError, OSError):
                pass
    if entry_points:
        summary.append("Likely entry points:")
        for ep in entry_points[:10]:
            summary.append("  " + ep)
    return "\n".join(summary)


def get_test_map(path="."):
    """List test files and heuristically map their names to likely source modules."""
    target = safe_path(path)
    if not target.is_dir():
        return "Test map path must be a directory."
    test_files = []
    for entry in target.rglob("test_*.py"):
        if entry.is_file():
            test_files.append(str(entry.relative_to(common.ROOT)))
    for entry in target.rglob("*_test.py"):
        if entry.is_file():
            test_files.append(str(entry.relative_to(common.ROOT)))
    if not test_files:
        return "No test files found (looking for test_*.py or *_test.py)."
    result = ["Test files found:"]
    for tf in sorted(test_files):
        result.append("  " + tf)
    return "\n".join(result)


def inspect_test_failure(output):
    """Extract likely failing test names, assertion errors, tracebacks and relevant context from supplied test output."""
    if not isinstance(output, str):
        return "output must be a string"
    lines = output.splitlines()
    failures = []
    current_test = None
    for i, line in enumerate(lines):
        # Look for pytest/unittest failure patterns
        if "FAILED" in line or "FAIL:" in line or "ERROR:" in line:
            current_test = line.strip()
            failures.append(current_test)
        elif current_test and ("AssertionError" in line or "assert " in line or "Traceback" in line):
            failures.append("  " + line.strip())
    return "\n".join(failures) if failures else "No clear test failures detected in output."


