# Roadmap: Project File and Path Access Control

## Goal

Make project-file access explicit and configurable. Users should be able to
control which paths Shai may view, edit, truncate or delete, with safe defaults
and narrower per-path exceptions.

## Proposed policy model

Use ordered path rules with a default policy. Rules should be evaluated against
canonical, resolved paths relative to the configured project root.

```json
{
  "project_access": {
    "default": {
      "view": true,
      "edit": true,
      "delete": false,
      "truncate": false
    },
    "paths": [
      {
        "path": "src/**",
        "view": true,
        "edit": true,
        "delete": false,
        "truncate": false
      },
      {
        "path": "secrets/**",
        "view": false,
        "edit": false,
        "delete": false,
        "truncate": false
      },
      {
        "path": "docs/generated/**",
        "view": true,
        "edit": false,
        "delete": false,
        "truncate": false
      }
    ]
  }
}
```

This is a proposal for a future release; it does not change current file access.

## Permission definitions

- `view`: list, stat, read and search file contents.
- `edit`: create or modify existing file contents without reducing them to
  empty content.
- `truncate`: replace a file with empty content or remove a substantial
  portion of its contents. Treat truncation as more sensitive than ordinary
  editing.
- `delete`: remove a file or directory. Directory deletion should be a separate
  high-risk operation and should not be inferred from file deletion permission.

## Rule semantics

- Paths are relative to the project root unless explicitly documented otherwise.
- Resolve symlinks before checking permissions; a path inside the project that
  points outside must not bypass the policy.
- Deny rules should take precedence over broad allow rules.
- Define and test rule ordering and glob semantics; avoid ambiguous last-match
  behaviour.
- Apply the same policy to `list_files`, `read_file`, `search_files`, edits,
  patches, line replacements, command execution and future delete tools.
- A parent directory listing must not leak names of descendants hidden by a
  `view=false` rule.
- Reject absolute paths and `..` traversal unless a future explicit external
  workspace mapping is added.

## User experience

- Explain which rule denied an operation and which permission was missing.
- Add a `/access` or `/permissions` command to inspect the effective policy.
- Provide a dry-run policy diagnostic for a given path and operation.
- Keep approvals separate from access control: approval cannot grant a
  permission that the path policy denies.

## Acceptance criteria

- Every file operation checks the same central policy function.
- Symlink and traversal tests prove the project boundary cannot be escaped.
- Hidden paths do not leak through directory listing or recursive search.
- Edit, truncate and delete are independently enforceable.
- A denied tool call returns a concise, actionable reason without retry loops.
