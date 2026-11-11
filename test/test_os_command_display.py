import contextlib
import io
import unittest
from unittest.mock import patch

from modules.ui.agent_cli import display_os_command_result


class OSCommandDisplayTests(unittest.TestCase):
    def test_success_displays_only_terminal_stdout(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            display_os_command_result({
                "status": "completed", "stdout": "On branch main\\n",
                "stderr": "", "exit_code": 0, "executable": "git",
                "arguments": ["status"], "cwd": "/project",
                "duration_seconds": 0.1,
            })
        self.assertEqual(stdout.getvalue(), "On branch main\\n")
        self.assertEqual(stderr.getvalue(), "")

    def test_success_with_no_output_is_clear(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            display_os_command_result({"status": "completed", "stdout": "", "stderr": "", "exit_code": 0})
        self.assertIn("completed successfully", stdout.getvalue())
        self.assertNotIn("{", stdout.getvalue())

    def test_failure_displays_streams_and_exit_code_without_json(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            display_os_command_result({
                "status": "failed", "stdout": "partial output",
                "stderr": "fatal: example", "exit_code": 128,
            })
        self.assertTrue(stdout.getvalue().startswith("partial output\\n"))
        self.assertIn("fatal: example", stderr.getvalue())
        self.assertIn("exit code 128", stdout.getvalue())
        self.assertNotIn('"status"', stdout.getvalue())

    def test_policy_error_is_readable(self):
        stdout = io.StringIO()
        with patch("modules.ui.agent_cli.colorize", side_effect=lambda value, _style: value):
            with contextlib.redirect_stdout(stdout):
                display_os_command_result({
                    "status": "blocked_confirmation_required",
                    "error": "git push is blocked by policy",
                })
        self.assertEqual(stdout.getvalue().strip(), "[Shai] git push is blocked by policy")


if __name__ == "__main__":
    unittest.main()
