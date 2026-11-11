"""Project-local runtime state and ignore-file management for Shai."""

import json
import pathlib

MANAGED_BEGIN = "# BEGIN SHAI MANAGED IGNORE RULES"
MANAGED_END = "# END SHAI MANAGED IGNORE RULES"
MANAGED_RULES = ("/logs/", "/shai_backups/", "/task.json", "/history.json")


def ensure_project_state(project_dir):
    """Create .shai and its narrow ignore rules without hiding other state files."""
    project_dir = pathlib.Path(project_dir).resolve()
    state_dir = project_dir / ".shai"
    state_dir.mkdir(parents=True, exist_ok=True)
    ignore_file = state_dir / ".gitignore"
    existing = ignore_file.read_text(encoding="utf-8") if ignore_file.exists() else ""
    lines = existing.splitlines()
    # Replace only the block managed by Shai; retain unrelated user rules/comments.
    if MANAGED_BEGIN in lines and MANAGED_END in lines:
        start = lines.index(MANAGED_BEGIN)
        end = lines.index(MANAGED_END, start + 1)
        lines = lines[:start] + lines[end + 1:]
    managed = [MANAGED_BEGIN, *MANAGED_RULES, MANAGED_END]
    if lines and lines[-1].strip():
        lines.append("")
    lines.extend(managed)
    desired = chr(10).join(lines).rstrip() + chr(10)
    if not ignore_file.exists() or ignore_file.read_text(encoding="utf-8") != desired:
        ignore_file.write_text(desired, encoding="utf-8")
    return state_dir


def migrate_legacy_model_selection(project_dir, legacy_config_file):
    """Migrate legacy provider/model selection into project model_routing.json.

    The old provider_model.json is consumed once and removed. Existing routing
    entries and a project-level default take precedence over legacy values.
    """
    project_dir = pathlib.Path(project_dir).resolve()
    state_dir = ensure_project_state(project_dir)
    routing_file = state_dir / "model_routing.json"
    legacy_selection_file = state_dir / "provider_model.json"
    legacy_config_file = pathlib.Path(legacy_config_file)

    routing = {}
    if routing_file.exists():
        try:
            routing = json.loads(routing_file.read_text(encoding="utf-8"))
            if not isinstance(routing, dict):
                routing = {}
        except (OSError, ValueError, TypeError):
            routing = {}
    if routing.get("version") != 1:
        previous = routing
        routing = {"version": 1}
        if isinstance(previous.get("default"), dict):
            routing["default"] = previous["default"]
        if isinstance(previous.get("models"), dict):
            routing["models"] = previous["models"]
    default = routing.get("default")
    default = dict(default) if isinstance(default, dict) else {}

    legacy = {}
    if legacy_selection_file.exists():
        try:
            legacy = json.loads(legacy_selection_file.read_text(encoding="utf-8"))
            if not isinstance(legacy, dict):
                legacy = {}
        except (OSError, ValueError, TypeError):
            legacy = {}

    shared = {}
    if legacy_config_file.exists():
        try:
            shared = json.loads(legacy_config_file.read_text(encoding="utf-8"))
            if not isinstance(shared, dict):
                shared = {}
        except (OSError, ValueError, TypeError):
            shared = {}

    changed = False
    if not (
        isinstance(default.get("provider"), str) and default["provider"].strip()
        and isinstance(default.get("model"), str) and default["model"].strip()
    ):
        candidate = legacy
        if not (
            isinstance(candidate.get("provider"), str) and candidate["provider"].strip()
            and isinstance(candidate.get("model"), str) and candidate["model"].strip()
        ):
            candidate = shared
        if (
            isinstance(candidate.get("provider"), str) and candidate["provider"].strip()
            and isinstance(candidate.get("model"), str) and candidate["model"].strip()
        ):
            default["provider"] = candidate["provider"]
            default["model"] = candidate["model"]
            changed = True
    if default:
        routing["default"] = default
    if changed or (default and not routing_file.exists()):
        routing_file.parent.mkdir(parents=True, exist_ok=True)
        routing_file.write_text(
            json.dumps(routing, indent=2, ensure_ascii=False) + chr(10),
            encoding="utf-8",
        )

    # Remove the old dedicated selection file after migration.
    if legacy_selection_file.exists():
        legacy_selection_file.unlink()

    # Remove deprecated top-level provider/model keys from legacy shared config.
    if isinstance(shared, dict) and ("provider" in shared or "model" in shared):
        shared.pop("provider", None)
        shared.pop("model", None)
        temporary = legacy_config_file.with_name(legacy_config_file.name + ".tmp")
        temporary.write_text(
            json.dumps(shared, indent=2, ensure_ascii=False) + chr(10),
            encoding="utf-8",
        )
        temporary.replace(legacy_config_file)

    # Preserve existing model-metadata migration behavior.
    legacy_model_file = legacy_config_file.parent / "model_config.json"
    state_model_file = state_dir / "model_config.json"
    if legacy_model_file.exists():
        if state_model_file.exists():
            try:
                old_data = json.loads(legacy_model_file.read_text(encoding="utf-8"))
                state_data = json.loads(state_model_file.read_text(encoding="utf-8"))
                if isinstance(old_data, dict) and isinstance(state_data, dict):
                    def merge_missing(target, source):
                        for key, value in source.items():
                            if key not in target:
                                target[key] = value
                            elif isinstance(target[key], dict) and isinstance(value, dict):
                                merge_missing(target[key], value)
                    merge_missing(state_data, old_data)
                    state_model_file.write_text(
                        json.dumps(state_data, indent=2, ensure_ascii=False) + chr(10),
                        encoding="utf-8",
                    )
            except (OSError, ValueError, TypeError):
                pass
            legacy_model_file.unlink()
        else:
            state_model_file.write_bytes(legacy_model_file.read_bytes())
            legacy_model_file.unlink()
    return state_dir

