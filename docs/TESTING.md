# Testing

Run all automated checks from the repository root:

```sh
sh ./run_all_tests.sh
```

`run_all_tests.sh` delegates to `test/run_tests.py`. The runner:

1. Discovers and runs every `test/test_*.py` unittest suite.
2. Runs the same test directory with pytest when pytest is installed.
3. Compiles `agent.py`, `chat.py`, `modules/`, and `test/` with `compileall`.
4. Returns a non-zero exit code if unittest, pytest (when available), or
   compilation fails.

## Generated reports

Reports are written to `test/` and overwritten on each run:

| File | Purpose |
|---|---|
| `test_report.txt` | Human-readable run summary and detailed test output |
| `test_report.json` | Machine-readable status, timestamps, counts, and per-test outcomes |
| `test_report_pytest.xml` | JUnit-compatible pytest report; generated only when pytest is installed |

The runner uses only the Python standard library. Pytest is an optional
additional test pass.
