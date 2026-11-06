"""Shared project-root state and centralized project path access policy."""
import re
import json
import pathlib

ROOT = pathlib.Path.cwd().resolve()
MAX_FILE_SIZE = 200_000
GENERIC_CONFIG_FILE = pathlib.Path(__file__).resolve().parents[2] / "assets" / "project_access_config.json"
PROJECT_GRANTS_FILE = None
DEFAULT_ACCESS_POLICY = {
    "default": {"view": True, "edit": True, "truncate": False, "delete": False, "execute": True},
    "paths": [],
}
ACCESS_PROMPT = None


class ProjectAccessDenied(PermissionError):
    """Raised when a project path does not grant the requested capability."""


def configure_root(path):
    global ROOT, PROJECT_GRANTS_FILE
    ROOT = pathlib.Path(path).expanduser().resolve()
    PROJECT_GRANTS_FILE = ROOT / ".shai" / "project_access.json"
    return ROOT


def configure_access_config(path):
    """Compatibility hook for tests/embedders; sets the generic config source."""
    global GENERIC_CONFIG_FILE
    GENERIC_CONFIG_FILE = pathlib.Path(path).expanduser().resolve()
    return GENERIC_CONFIG_FILE


def _clean_rules(rules, defaults):
    clean = []
    if not isinstance(rules, list):
        return clean
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("path"), str):
            continue
        path_pattern = rule["path"].replace("\\", "/").strip("/")
        if (not path_pattern or path_pattern.startswith("/") or
                ".." in pathlib.PurePosixPath(path_pattern).parts):
            continue
        permissions = {key: rule[key] for key in defaults
                       if isinstance(rule.get(key), bool)}
        clean.append({"path": path_pattern, **permissions})
    return clean


def _read_json(path):
    try:
        with pathlib.Path(path).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _grants_path():
    return PROJECT_GRANTS_FILE or (ROOT / ".shai" / "project_access.json")


def load_access_policy():
    """Merge generic rules from project_access_config with project-local grants."""
    generic = _read_json(GENERIC_CONFIG_FILE).get("project_access", {})
    if not isinstance(generic, dict):
        generic = {}
    defaults = dict(DEFAULT_ACCESS_POLICY["default"])
    supplied = generic.get("default", {})
    if isinstance(supplied, dict):
        for key in defaults:
            if isinstance(supplied.get(key), bool):
                defaults[key] = supplied[key]
    generic_rules = _clean_rules(generic.get("paths", []), defaults)
    grants_data = _read_json(_grants_path())
    grant_rules = _clean_rules(grants_data.get("grants", []), defaults)
    return {"default": defaults, "paths": generic_rules, "grants": grant_rules}


def _save_grant(path_pattern, permissions):
    grants_path = _grants_path()
    grants_path.parent.mkdir(parents=True, exist_ok=True)
    data = _read_json(grants_path)
    grants = data.get("grants", [])
    if not isinstance(grants, list):
        grants = []
    # Merge grants for the same path; an explicit user grant is authoritative.
    for rule in grants:
        if isinstance(rule, dict) and rule.get("path") == path_pattern:
            rule.update(permissions)
            break
    else:
        grants.append({"path": path_pattern, **permissions})
    data["version"] = 1
    data["grants"] = grants
    temporary = grants_path.with_suffix(grants_path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(grants_path)


def _request_interactive_access(path, permission):
    """Ask for scoped permission in a real interactive terminal only.

    Tests may inject ACCESS_PROMPT to exercise the flow, but the test runner
    itself is never allowed to fall through to stdin/input().
    """
    import os
    import sys

    if not getattr(sys.stdin, "isatty", lambda: False)():
        return False
    if ACCESS_PROMPT is None:
        if "PYTEST_CURRENT_TEST" in os.environ or "unittest" in sys.modules:
            return False
        prompt = input
    else:
        prompt = ACCESS_PROMPT

    relative = pathlib.Path(path).as_posix().strip("/") or "."
    labels = {"view": "read", "edit": "write", "truncate": "write (truncate)",
              "delete": "delete", "execute": "exec"}
    requested_code = {"view": "r", "edit": "w", "truncate": "w",
                      "delete": "d", "execute": "x"}.get(permission, "?")
    print("\nShai is blocked from accessing a project path.")
    print("  Path:       {}".format(relative))
    print("  Requested:  {} ({})".format(requested_code, labels.get(permission, permission)))
    print("  Scope:      [1] this file/folder  [2] parent  [3] level-2 parent  [4] deny")
    try:
        scope = prompt("Grant scope [1-4]: ").strip().lower()
    except (EOFError, KeyboardInterrupt, OSError):
        return False
    if scope in ("4", "deny", "d", ""):
        return False
    target = pathlib.PurePosixPath(relative)
    if scope in ("1", "exact", "file", "folder"):
        base = target.as_posix()
    elif scope in ("2", "parent", "p"):
        base = target.parent.as_posix()
    elif scope in ("3", "parent2", "level-2", "grandparent", "l2"):
        base = target.parent.parent.as_posix()
    else:
        print("Invalid scope; access remains denied.")
        return False
    if base in ("", "."):
        base = "."

    print("  Access bits: r=read, w=write, x=exec, d=delete, a=all")
    try:
        choice = prompt("Grant permissions [rwxda; e.g. rwx]: ").strip().lower()
    except (EOFError, KeyboardInterrupt, OSError):
        return False

    aliases = {
        "read": "r", "view": "r", "write": "w", "edit": "w",
        "exec": "x", "execute": "x", "delete": "d", "all": "a",
    }
    # Accept compact Unix-style bits (rwx, rw, x, etc.) and retain the
    # previous comma-separated words as a backwards-compatible input form.
    if choice in aliases:
        choice = aliases[choice]
    elif "," in choice or any(ch.isspace() for ch in choice):
        tokens = [part for part in re.split(r"[,\s]+", choice) if part]
        normalized = ""
        for token in tokens:
            if token not in aliases:
                print("Invalid permission '{}'; access remains denied.".format(token))
                return False
            normalized += aliases[token]
        choice = normalized
    if choice == "a":
        selected = {"view", "edit", "truncate", "delete", "execute"}
    elif choice and all(ch in "rwxd" for ch in choice):
        selected = set()
        if "r" in choice:
            selected.add("view")
        if "w" in choice:
            selected.update(("edit", "truncate"))
        if "x" in choice:
            selected.add("execute")
        if "d" in choice:
            selected.add("delete")
    else:
        print("Invalid permission selection; access remains denied.")
        return False

    if permission not in selected:
        print("Requested capability '{}' was not selected; no grant was saved.".format(
            labels.get(permission, permission)
        ))
        return False
    # Folder scopes include descendants; exact scopes stay exact unless the
    # selected exact target is itself a directory.
    is_directory_scope = (scope in ("2", "parent", "p", "3", "parent2", "level-2", "grandparent", "l2") or
                         ((ROOT / base).is_dir() if base != "." else True))
    rule_path = ("**" if base == "." else base + "/**") if is_directory_scope else base
    permissions = {name: name in selected for name in
                   ("view", "edit", "truncate", "delete", "execute")}
    _save_grant(rule_path, permissions)
    print("Access granted for {}: {}".format(base, "".join(
        bit for bit, name in (("r", "view"), ("w", "edit"), ("x", "execute"), ("d", "delete"))
        if name in selected
    )))
    return permission in selected

def _relative_path(target, root):
    try:
        return target.relative_to(root).as_posix() or "."
    except ValueError:
        raise ProjectAccessDenied("Path resolves outside the configured project root.")


def _glob_matches(path, pattern):
    """Match path globs with shell-like segment semantics; only ** crosses '/'."""
    pieces = []
    index = 0
    while index < len(pattern):
        if pattern[index:index + 2] == "**":
            if pattern[index + 2:index + 3] == "/":
                pieces.append("(?:.*/)?")
                index += 3
                continue
            pieces.append(".*")
            index += 2
            continue
        char = pattern[index]
        if char == "*":
            pieces.append("[^/]*")
        elif char == "?":
            pieces.append("[^/]")
        else:
            pieces.append(re.escape(char))
        index += 1
    return re.fullmatch("".join(pieces), path) is not None


def access_decision(path, permission, root=None):
    """Return (allowed, matched_rule); project-local grants override generic policy."""
    if not isinstance(path, (str, pathlib.Path)) or not str(path).strip():
        raise ValueError("Project path must be a non-empty relative path.")
    if permission not in ("view", "edit", "truncate", "delete", "execute"):
        raise ValueError("Unknown project permission: {}".format(permission))
    project_root = pathlib.Path(root or ROOT).expanduser().resolve()
    raw = pathlib.Path(path)
    if raw.is_absolute() or ".." in raw.parts:
        raise ProjectAccessDenied("Project paths must be relative and cannot contain '..'.")
    target = (project_root / raw).resolve()
    relative = _relative_path(target, project_root)
    policy = load_access_policy()
    decision = policy["default"].get(permission, False)
    matched = "default"
    generic_denied = False
    for rule in policy["paths"]:
        pattern = rule["path"]
        if _glob_matches(relative, pattern) or (
            pattern.endswith("/**") and relative == pattern[:-3].rstrip("/")
        ):
            if permission in rule:
                if rule[permission] is False:
                    generic_denied = True
                    decision, matched = False, pattern
                elif rule[permission] is True and not generic_denied:
                    decision, matched = True, pattern
    # Explicit user grants in ROOT/.shai are evaluated last and can authorize
    # a path that generic policy intentionally blocks.
    for rule in policy.get("grants", []):
        pattern = rule["path"]
        if _glob_matches(relative, pattern) or (
            pattern.endswith("/**") and relative == pattern[:-3].rstrip("/")
        ):
            if rule.get(permission) is True:
                decision, matched = True, "user grant: " + pattern
            elif rule.get(permission) is False:
                decision, matched = False, "user denial: " + pattern
    return decision, matched


def require_access(path, permission, root=None, prompt=True):
    allowed, rule = access_decision(path, permission, root=root)
    if allowed:
        return True
    if prompt and _request_interactive_access(path, permission):
        allowed, rule = access_decision(path, permission, root=root)
        if allowed:
            return True
    raise ProjectAccessDenied(
        "Project path access denied: '{}' requires '{}' permission (matched rule: {}).".format(
            path, permission, rule
        )
    )

def safe_path(path):
    if not isinstance(path, (str, pathlib.Path)) or not str(path).strip():
        raise ValueError("Project path must be a non-empty relative path.")
    candidate = pathlib.Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("Path must be project-relative and cannot contain '..'.")
    target = (ROOT / candidate).resolve()
    try:
        target.relative_to(ROOT)
    except ValueError:
        raise ValueError("Path escapes project root.")
    return target
