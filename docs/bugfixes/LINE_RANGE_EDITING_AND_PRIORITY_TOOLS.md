# Line-Range Editing and Priority Tools

## Why `replace_lines` was added

`patch_file` requires `old_text` to match exactly once. Indentation, whitespace, or line-ending differences can cause a zero-match result even when the intended code is visible in the preceding read.

`replace_lines` provides a positional alternative:
- `start_line` and `end_line` are 1-based and inclusive.
- Shai should read the latest file contents before using it.
- The operation still shows a diff, follows approval settings, validates supported file types, creates a backup, and writes atomically.
- If the range is stale or outside the file, the tool returns an error without changing the file.

After a failed exact patch, Shai is instructed to inspect the current lines and switch to `replace_lines`, not repeat the same patch unchanged.

## Added project tools

This release adds Python structure navigation, textual reference search, project/test maps, combined verification, test-failure extraction, and an in-memory task checklist. See [TOOLS.md](../TOOLS.md) for exact parameters and limitations.

## Verification

The added tools have unit tests covering registration, schemas, Python outlines, symbol lookup, project/test maps, failure extraction, task-plan formatting, line replacement, and verification dispatch.
