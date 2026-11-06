"""Configurable, bounded previews for file changes in the terminal UI."""

import json
import pathlib


def load_settings(config_path=None):
    defaults = {
        "enabled": True,
        "max_diff_lines": 160,
        "max_diff_chars": 16000,
        "preserve_head_lines": 90,
        "preserve_tail_lines": 40,
    }
    supplied = None
    if config_path is not None:
        path = pathlib.Path(config_path)
        try:
            with path.open("r", encoding="utf-8") as handle:
                config = json.load(handle)
            if isinstance(config, dict):
                if "file_change_display" in config and isinstance(config["file_change_display"], dict):
                    supplied = config["file_change_display"]
                elif path.name != "cli_config.json" and (path.parent / "cli_config.json").is_file():
                    with (path.parent / "cli_config.json").open("r", encoding="utf-8") as cli_handle:
                        cli_config = json.load(cli_handle)
                    if isinstance(cli_config, dict) and isinstance(cli_config.get("file_change_display"), dict):
                        supplied = cli_config["file_change_display"]
        except (OSError, ValueError, TypeError):
            pass
    if supplied is None:
        cli_config_path = pathlib.Path(__file__).resolve().parent.parent / "assets" / "cli_config.json"
        try:
            with cli_config_path.open("r", encoding="utf-8") as handle:
                cli_config = json.load(handle)
            if isinstance(cli_config, dict) and isinstance(cli_config.get("file_change_display"), dict):
                supplied = cli_config["file_change_display"]
        except (OSError, ValueError, TypeError):
            pass
        if supplied is None:
            shai_config_path = pathlib.Path(__file__).resolve().parent.parent / "assets" / "shai_config.json"
            try:
                with shai_config_path.open("r", encoding="utf-8") as handle:
                    shai_config = json.load(handle)
                if isinstance(shai_config, dict) and isinstance(shai_config.get("file_change_display"), dict):
                    supplied = shai_config["file_change_display"]
            except (OSError, ValueError, TypeError):
                pass
    if isinstance(supplied, dict):
        defaults.update(supplied)
    for key in ("max_diff_lines", "max_diff_chars", "preserve_head_lines", "preserve_tail_lines"):
        value = defaults.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            defaults[key] = 1
    defaults["enabled"] = defaults.get("enabled") is True
    return defaults


def _bounded_diff(diff, settings):
    lines = diff.splitlines(True)
    original_chars = len(diff)
    max_lines = settings["max_diff_lines"]
    max_chars = settings["max_diff_chars"]
    if len(lines) <= max_lines and original_chars <= max_chars:
        return diff, False, 0, 0

    head_count = min(settings["preserve_head_lines"], max_lines)
    tail_count = min(settings["preserve_tail_lines"], max(0, max_lines - head_count))
    if head_count + tail_count == 0:
        head_count = 1
    head_text = "".join(lines[:head_count])
    tail_text = "".join(lines[-tail_count:]) if tail_count else ""
    omitted_lines = max(0, len(lines) - head_count - tail_count)

    marker_template = "\n... [truncated: {} lines / {} characters omitted] ...\n"
    marker = marker_template.format(omitted_lines, 0)
    # Reserve room for the marker, then retain the start and end of the diff.
    if len(marker) >= max_chars:
        marker = marker[:max_chars]
        head_room = tail_room = 0
    else:
        available = max_chars - len(marker)
        head_room = min(len(head_text), (available + 1) // 2)
        tail_room = min(len(tail_text), available - head_room)
        if head_room < len(head_text):
            tail_room = min(len(tail_text), available - head_room)
        if head_room + tail_room < available:
            head_room = min(len(head_text), available - tail_room)

    retained_head = head_text[:head_room]
    retained_tail = tail_text[-tail_room:] if tail_room else ""
    omitted_chars = max(0, original_chars - len(retained_head) - len(retained_tail))
    marker = marker_template.format(omitted_lines, omitted_chars)
    if len(marker) > max_chars:
        marker = marker[:max_chars]
        retained_head = ""
        retained_tail = ""
    elif len(retained_head) + len(retained_tail) + len(marker) > max_chars:
        excess = len(retained_head) + len(retained_tail) + len(marker) - max_chars
        if len(retained_tail) >= excess:
            retained_tail = retained_tail[excess:]
        else:
            excess -= len(retained_tail)
            retained_tail = ""
            retained_head = retained_head[:-excess] if excess else retained_head
        omitted_chars = max(0, original_chars - len(retained_head) - len(retained_tail))
        marker = marker_template.format(omitted_lines, omitted_chars)
    return retained_head + marker + retained_tail, True, omitted_lines, omitted_chars


def print_change_preview(diff, heading, summary, config_path=None):
    settings = load_settings(config_path)
    print("\n--- {} ---".format(heading))
    print(summary)
    if not settings["enabled"]:
        print("Diff preview disabled (file_change_display.enabled=false).")
    else:
        preview, truncated, omitted_lines, omitted_chars = _bounded_diff(diff, settings)
        print(preview or "(No changes)")
        if truncated:
            print("[Interface preview truncated: {} lines and {} characters omitted.]".format(
                omitted_lines, omitted_chars
            ))
    print("------------------------")
