#!/bin/sh
# Run all Shai tests and write reports under test/.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"
exec python3 test/run_tests.py
