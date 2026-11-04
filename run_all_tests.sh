#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"
PYTHON=${PYTHON:-python3}
"$PYTHON" -m unittest discover -s test -v
