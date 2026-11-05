#!/usr/bin/env python3
"""Compatibility entry point for the modular Shai coding-agent engine."""
import sys
from modules.engine import agent_runtime as _runtime

if __name__ == "__main__":
    _runtime.main()
else:
    # Keep legacy imports and monkeypatch-based integrations bound to the
    # actual runtime module namespace.
    sys.modules[__name__] = _runtime
