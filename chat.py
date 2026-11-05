#!/usr/bin/env python3
"""Compatibility entry point for Shai's modular chat UI."""
import sys
from modules.ui import chat_runtime as _runtime

if __name__ == "__main__":
    _runtime.main()
else:
    sys.modules[__name__] = _runtime
