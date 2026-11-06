#!/usr/bin/env python3
"""Compatibility and CLI entry point for the Shai coding agent."""
import sys

if __name__ == "__main__":
    from modules.ui.agent_cli import main
    sys.exit(main())
else:
    # Keep legacy imports and monkeypatch-based integrations bound to the
    # actual runtime module namespace.
    from modules.engine import agent_runtime as _runtime
    sys.modules[__name__] = _runtime
