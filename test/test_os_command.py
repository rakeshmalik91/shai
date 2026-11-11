import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from modules.tools.os_command import execute_os_command, parse_os_command_line


class OSCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = {
            "enabled": True, "default_timeout_seconds": 30,
            "max_timeout_seconds": 60, "requires_approval": False,
            "allowed_commands": ["python", "pytest", "grep", "sed", "awk", "git"],
            "unrestricted_commands": {"common": [], "unix": [], "windows": []},
            "blocked_commands": {"common": [], "unix": ["sudo", "su", "rm", "rmdir"], "windows": []},
            "blocked_command_rules": {
                "common": {},
                "unix": {"git": ["reset", "push", "clean", "checkout --", "restore"]},
                "windows": {},
            },
            "blocked_command_exceptions": {
                "common": {},
                "unix": {"sudo": ["--help", "--version"], "su": ["--help", "--version"]},
                "windows": {},
            },
        }
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, command, cwd, timeout, output_settings, env=None):
        self.calls.append((command, cwd, timeout, output_settings, env))
        return {"timed_out": False, "returncode": 0, "stdout": "ok\n", "stderr": "",
                "duration_seconds": 0.01, "output_truncated": False,
                "stdout_total_bytes": 3, "stderr_total_bytes": 0}

    def execute(self, executable, args, **kwargs):
        return execute_os_command(executable, args, root=self.root, settings=self.settings,
                                  confirm=lambda preview: True, runner=self.runner, **kwargs)

    def test_configured_command_runs_with_argument_vector(self):
        result = self.execute("grep", ["-n", "bird", "notes.txt"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.calls[0][0][-4:], [self.calls[0][0][0], "-n", "bird", "notes.txt"])
        self.assertEqual(self.calls[0][1], self.root)

    def test_configured_commands_are_not_restricted_by_hardcoded_argument_rules(self):
        result = self.execute("python", ["-c", "print('hello')"])
        self.assertEqual(result["status"], "completed")

    def test_unrestricted_command_skips_routine_approval(self):
        self.settings["unrestricted_commands"] = {"common": [], "unix": ["grep"], "windows": []}
        self.settings["requires_approval"] = True
        def reject(_preview):
            raise AssertionError("Unrestricted command must not prompt for routine approval")
        result = execute_os_command(
            "grep", ["bird", "notes.txt"], root=self.root, settings=self.settings,
            confirm=reject, runner=self.runner
        )
        self.assertEqual(result["status"], "completed")

    def test_unrestricted_command_does_not_bypass_blocked_rule(self):
        self.settings["unrestricted_commands"] = {"common": [], "unix": ["git"], "windows": []}
        result = self.execute("git", ["push", "origin", "main"])
        self.assertEqual(result["status"], "blocked_confirmation_required")

    def test_blocked_rules_are_configured_as_strings(self):
        self.assertEqual(
            self.settings["blocked_command_rules"]["unix"]["git"],
            ["reset", "push", "clean", "checkout --", "restore"]
        )

    def test_nested_platform_policy_and_unrestricted_readonly_command(self):
        platform_settings = {
            "enabled": True,
            "requires_approval": True,
            "default_timeout_seconds": 30,
            "max_timeout_seconds": 120,
            "platforms": {
                "common": {
                    "allowed_commands": ["git"],
                    "unrestricted_commands": [],
                    "blocked_commands": [],
                    "blocked_command_exceptions": {},
                    "blocked_command_rules": {},
                    "blocked_command_rule_exceptions": {},
                },
                "unix": {
                    "allowed_commands": ["grep"],
                    "unrestricted_commands": ["grep"],
                    "blocked_commands": ["rm"],
                    "blocked_command_exceptions": {},
                    "blocked_command_rules": {"git": ["push"]},
                    "blocked_command_rule_exceptions": {},
                },
                "windows": {
                    "allowed_commands": [],
                    "unrestricted_commands": [],
                    "blocked_commands": [],
                    "blocked_command_exceptions": {},
                    "blocked_command_rules": {},
                    "blocked_command_rule_exceptions": {},
                },
            },
        }
        result = execute_os_command(
            "grep", ["bird", "notes.txt"], root=self.root, settings=platform_settings,
            confirm=lambda _preview: self.fail("unrestricted read-only command prompted"),
            runner=self.runner,
        )
        self.assertEqual(result["status"], "completed")

    def test_unconfigured_command_requests_whitelisting(self):
        result = self.execute("ipconfig", ["/all"])
        self.assertEqual(result["status"], "whitelist_required")
        self.assertEqual(len(self.calls), 0)

    def test_explicitly_blocked_command_cannot_be_whitelisted(self):
        result = self.execute("sudo", ["anything"])
        self.assertEqual(result["status"], "blocked_confirmation_required")
        self.assertEqual(len(self.calls), 0)

    def test_blocked_command_allows_only_exact_informational_exception(self):
        with patch("modules.tools.os_command.shutil.which", return_value="/usr/bin/sudo"):
            result = self.execute("sudo", ["--version"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.calls[0][0][1:], ["--version"])

    def test_blocked_command_exception_does_not_allow_extra_arguments(self):
        result = self.execute("sudo", ["--version", "whoami"])
        self.assertEqual(result["status"], "blocked_confirmation_required")
        self.assertEqual(len(self.calls), 0)

    def test_blocked_command_without_exception_requires_interactive_permission(self):
        result = self.execute("su", ["-"])
        self.assertEqual(result["status"], "blocked_confirmation_required")
        self.assertEqual(len(self.calls), 0)

    def test_destructive_git_subcommands_are_blocked_but_read_only_commands_work(self):
        for args in (["reset", "--hard"], ["push", "origin", "main"], ["clean", "-fd"],
                     ["-C", "repo", "push", "origin", "main"]):
            result = self.execute("git", args)
            self.assertEqual(result["status"], "blocked_confirmation_required")
        self.assertEqual(len(self.calls), 0)
        self.assertEqual(self.execute("git", ["status"])["status"], "completed")

    def test_allow_blocked_invocation_once_does_not_change_config(self):
        with patch("modules.tools.os_command.shutil.which", return_value="/usr/bin/rm"):
            result = execute_os_command(
                "rm", ["temporary.txt"], root=self.root, settings=self.settings,
                confirm=lambda preview: True, runner=self.runner, allow_blocked_once=True)
        self.assertEqual(result["status"], "completed")
        platform_key = "windows" if __import__("os").name == "nt" else "unix"
        self.assertIn("rm", self.settings["blocked_commands"][platform_key])

    def test_shell_syntax_is_passed_as_literal_arguments_not_interpreted(self):
        self.execute("grep", ["x", "file", "|", "cat"])
        self.assertEqual(self.calls[0][0][-4:], ["x", "file", "|", "cat"])

    def test_approval_preview_contains_complete_command(self):
        self.settings["requires_approval"] = True
        previews = []
        result = execute_os_command("grep", ["-n", "bird"], root=self.root,
                                    settings=self.settings,
                                    confirm=lambda preview: previews.append(preview) or False,
                                    runner=self.runner)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("grep -n bird", previews[0])
        self.assertEqual(len(self.calls), 0)

    def test_timeout_is_bounded(self):
        self.execute("git", ["status"], timeout_seconds=999)
        self.assertEqual(self.calls[0][2], 60)

    def test_invalid_cwd_is_rejected(self):
        result = self.execute("grep", [], cwd="../")
        self.assertIn("inside the project", result["error"])

    def test_parse_command_line(self):
        self.assertEqual(parse_os_command_line("grep -n 'some text' file"),
                         ("grep", ["-n", "some text", "file"]))


if __name__ == "__main__":
    unittest.main()
