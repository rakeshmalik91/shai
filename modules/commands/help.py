"""User-facing command help."""
HELP_TEXT = """
Examples:
  Show my Git status.
  Switch to read-only mode.
  Show my task list.
  Explain the project structure.
  Find where the login screen is implemented.
  Fix the bug in main.py.
  Review my current Git changes.
  Run the Python tests.

Commands:
  /model   - Switch provider and model
  /switch  - Alias for /model
  /status  - Git status
  /files   - List project files
  /diff    - Show Git diff
  /reset   - Clear conversation history
  /ask     - Switch to ask mode (chat only, no tools)
  /read-only - Switch to read-only mode (read-only project tools)
  /ro      - Alias for /read-only
  /code    - Switch to code mode (agent with tools)
  /todo    - Show current task plan
  /task    - Alias for /todo
  /commit  - Commit staged changes (use --message-only to only generate a message)
  /push    - Push commits to remote
  /commit-push - Commit and push in one command
  /project - Switch project folder
  /exec - Run a controlled, allowlisted OS command 
  /os-command - Alias for /exec
  /exit    - Exit Shai

Natural-language requests can select known Shai commands through the
shai_command feature. Its canonical registry is assets/command_registry.json.

The os_command tool is available only in Agent mode. It is not a shell: only
allowlisted Git inspection and Python verification profiles are supported.
All execution uses argument arrays, project-root checks, time/output limits,
and approval policy.

Press Ctrl+D while Shai is processing to cancel the current iteration
and return to the chat prompt.

Configure os_command and shai_command in command_config.json;
max_tool_call_iterations, tool_safeguards, retry, and granular approval policy in shai_config.json.
"""
