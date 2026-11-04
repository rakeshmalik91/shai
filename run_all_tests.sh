#!/bin/sh
# Portable test runner for iSH/Alpine and other POSIX shells.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'ERROR: python3 is required but was not found.' >&2
    exit 127
fi

printf '%s\n' '=== Shai test suite ==='
printf 'Python: '
python3 --version

printf '\n%s\n' '=== Unit tests (unittest) ==='
python3 -m unittest discover -s test -p 'test_*.py' -v

printf '\n%s\n' '=== Optional pytest ==='
if python3 -c 'import pytest' >/dev/null 2>&1; then
    python3 -m pytest -q test
else
    printf '%s\n' 'pytest is not installed; skipping (unittest is the required test runner).'
fi

printf '\n%s\n' '=== Syntax compilation ==='
python3 -m compileall -q agent.py chat.py modules test

printf '\n%s\n' 'All Shai tests and syntax checks passed.'
