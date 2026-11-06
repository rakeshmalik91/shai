# Project File and Path Access Control

## Status

Implemented. Generic policy lives in the `project_access` section of
`assets/shai_config.json`; user-approved project-specific grants live in
`.shai/project_access.json`. Enforcement is provided by `modules.tools.common` across filesystem tools, code inspection,
file editing and controlled command execution.

## Policy model

See `docs/CONFIGURATION.md` for the full schema and semantics. The supported
permissions are `view`, `edit`, `truncate`, `delete`, and `execute`. Generic
rules are project-relative glob patterns; generic explicit denies win over
other generic allows. Project-local user grants are evaluated last and can
resolve a generic block. The default policy permits view/edit/execute and
denies truncate/delete.

## Enforcement

- Canonicalize paths and reject absolute paths, parent traversal and symlink
  escapes.
- Centralize decisions in `access_decision`, `require_access`, and
  `load_access_policy`.
- Filter directory listings and recursively prune denied directories during
  search.
- Require `view` for file inspection and AST/project inspection.
- Require `edit` for patch, line replacement and full-file replacement.
- Require `truncate` for high-risk content reductions, independently of user
  approval.
- Require `execute` for controlled command working directories and Python
  execution targets; Git pathspecs require `view`.
- Approval settings cannot override path policy.
- Interactive blocks ask for exact path/folder, parent, level-2 parent, or deny,
  followed by read/write/delete/exec/all capability selection.
- User grants persist under the active project's `.shai/project_access.json`;
  non-interactive runs never prompt.

## Deliberate limitations

`delete` is reserved for future delete operations; there is currently no
general-purpose delete tool. This policy does not claim to sandbox arbitrary
Python test code or provide OS-level filesystem isolation. Controlled command
execution remains subject to its separate executable/argument allowlists.

## Verification

Run `python3 -m unittest discover -s test -v`. The dedicated access-control
tests cover deny precedence, traversal, symlink escapes, hidden listings,
recursive search, malformed configuration, interactive scope/capability grants,
non-interactive denial and permission diagnostics.
