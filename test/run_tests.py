#!/usr/bin/env python3
"""Run the complete Shai test suite and write durable reports under test/."""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = ROOT / "test"
TEXT_REPORT = TEST_DIR / "test_report.txt"
JSON_REPORT = TEST_DIR / "test_report.json"
PYTEST_XML = TEST_DIR / "test_report_pytest.xml"


class RecordingResult(unittest.TextTestResult):
    """TextTestResult with structured outcome details."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.details = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.details.append({"test": str(test), "status": "passed"})

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.details.append({
            "test": str(test), "status": "failed",
            "traceback": self._exc_info_to_string(err, test),
        })

    def addError(self, test, err):
        super().addError(test, err)
        self.details.append({
            "test": str(test), "status": "error",
            "traceback": self._exc_info_to_string(err, test),
        })

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.details.append({"test": str(test), "status": "skipped", "reason": reason})


def run_unittest():
    output = io.StringIO()
    stream = output
    runner = unittest.TextTestRunner(
        stream=stream, verbosity=2, resultclass=RecordingResult
    )
    suite = unittest.defaultTestLoader.discover(
        start_dir=str(TEST_DIR), pattern="test_*.py", top_level_dir=str(ROOT)
    )
    result = runner.run(suite)
    return {
        "output": output.getvalue(),
        "success": result.wasSuccessful(),
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "details": result.details,
    }


def run_optional_pytest():
    if not __import__("importlib").util.find_spec("pytest"):
        return {
            "available": False, "success": True,
            "output": "pytest is not installed; optional pytest run skipped.\n",
            "returncode": None,
        }
    command = [
        sys.executable, "-m", "pytest", "-v", str(TEST_DIR),
        f"--junitxml={PYTEST_XML}",
    ]
    proc = subprocess.run(
        command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return {
        "available": True, "success": proc.returncode == 0,
        "output": proc.stdout, "returncode": proc.returncode,
        "junit_xml": str(PYTEST_XML.relative_to(ROOT)),
    }


def run_compile_check():
    command = [
        sys.executable, "-m", "compileall", "-q",
        "agent.py", "chat.py", "modules", "test",
    ]
    proc = subprocess.run(
        command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return {
        "success": proc.returncode == 0,
        "output": proc.stdout or ("Compilation passed.\n" if proc.returncode == 0 else ""),
        "returncode": proc.returncode,
    }


def main():
    os.chdir(ROOT)
    started = dt.datetime.now().astimezone()
    sections = []
    print("=== Shai complete test run ===")
    print(f"Started: {started.isoformat()}")
    print(f"Python: {sys.version.split()[0]}")

    print("\n[1/3] unittest discovery...")
    unittest_result = run_unittest()
    print(unittest_result["output"], end="")
    sections.append(("UNITTEST", unittest_result["output"]))

    print("\n[2/3] optional pytest...")
    pytest_result = run_optional_pytest()
    print(pytest_result["output"], end="")
    sections.append(("PYTEST", pytest_result["output"]))

    print("\n[3/3] Python syntax compilation...")
    compile_result = run_compile_check()
    print(compile_result["output"], end="")
    sections.append(("COMPILE", compile_result["output"]))

    finished = dt.datetime.now().astimezone()
    success = (
        unittest_result["success"]
        and pytest_result["success"]
        and compile_result["success"]
    )
    status = "PASSED" if success else "FAILED"

    report_lines = [
        "SHAI TEST REPORT",
        "=" * 72,
        f"Status: {status}",
        f"Started: {started.isoformat()}",
        f"Finished: {finished.isoformat()}",
        f"Duration: {(finished - started).total_seconds():.2f} seconds",
        f"Python: {sys.version.split()[0]}",
        "",
        "UNITTEST",
        "-" * 72,
        f"Status: {'PASSED' if unittest_result['success'] else 'FAILED'}",
        f"Run: {unittest_result['tests_run']} | Failures: {unittest_result['failures']} "
        f"| Errors: {unittest_result['errors']} | Skipped: {unittest_result['skipped']}",
        unittest_result["output"],
        "PYTEST",
        "-" * 72,
        f"Status: {'SKIPPED (not installed)' if not pytest_result['available'] else ('PASSED' if pytest_result['success'] else 'FAILED')}",
        pytest_result["output"],
        "SYNTAX COMPILATION",
        "-" * 72,
        f"Status: {'PASSED' if compile_result['success'] else 'FAILED'}",
        compile_result["output"],
        "",
    ]
    TEXT_REPORT.write_text("\n".join(report_lines), encoding="utf-8")

    structured = {
        "status": status.lower(),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 2),
        "python_version": sys.version.split()[0],
        "unittest": {k: v for k, v in unittest_result.items() if k != "output"},
        "pytest": {k: v for k, v in pytest_result.items() if k != "output"},
        "compile": {k: v for k, v in compile_result.items() if k != "output"},
        "report": str(TEXT_REPORT.relative_to(ROOT)),
    }
    JSON_REPORT.write_text(json.dumps(structured, indent=2) + "\n", encoding="utf-8")

    print(f"\nOverall result: {status}")
    print(f"Text report: {TEXT_REPORT.relative_to(ROOT)}")
    print(f"JSON report: {JSON_REPORT.relative_to(ROOT)}")
    if pytest_result["available"]:
        print(f"JUnit report: {PYTEST_XML.relative_to(ROOT)}")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
