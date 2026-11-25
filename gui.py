#!/usr/bin/env python3
"""Desktop GUI entry point for Shai."""
import pathlib
import sys

_REPO_ROOT = str(pathlib.Path(__file__).resolve().parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from modules.ui.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
