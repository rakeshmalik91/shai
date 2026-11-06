import tempfile
import unittest
from pathlib import Path

from modules.tools.os_command import (
    OSCommandRejected,
    execute_os_command,
    parse_os_command_line,
)


class OSCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "test").mkdir()
        self.settings = {
            "enabled": True,
            "default_timeout_seconds": 30,
            "max_timeout_seconds": 60,
            "max_stdout_bytes": 1000,
            "max_stderr_bytes": 500,
            "preserve_output_head_bytes": 100,
            "preserve_output_tail_bytes": 100,
            "terminate_child_processes": True,
            "commands": {
                "git": {"enabled": True, "subcommands": [
                    "status", "diff", "log", "branch", "rev-parse", "show"
                ], "requires_approval": False},
                "python3": {"enabled": True, "modules": [
                    "pytest", "unittest", "compileall"
                ], "requires_approval": True},
            },
        }
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, command, cwd, timeout, output_settings, env=None):
        self.calls.append((command, cwd, timeout, output_settings, env))
        return {
            "timed_out": False, "returncode": 0, "stdout": "ok\n",
            "stderr": "", "duration_seconds": 0.01,
            "output_truncated": False, "stdout_total_bytes": 3,
            "stderr_total_bytes": 0,
        }

    def execute(self, executable, args, **kwargs):
        return execute_os_command(
            executable, args, root=self.root, settings=self.settings,
            confirm=lambda preview: True, runner=self.runner, **kwargs
        )

    def test_git_read_only_command_uses_argument_vector_and_bounded_runner(self):
        result = self.execute("git", ["status", "--short", "--branch"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.calls[0][0][-3:], ["status", "--short", "--branch"])
        self.assertEqual(self.calls[0][1], self.root)
        self.assertEqual(self.calls[0][4]["GIT_CONFIG_NOSYSTEM"], "1")

    def test_git_diff_disables_external_diff_and_textconv(self):
        result = self.execute("git", ["diff", "--stat"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.calls[0][0][-4:], ["diff", "--no-ext-diff", "--no-textconv", "--stat"])

    def test_python_test_command_requires_approval(self):
        approved = []
        result = execute_os_command(
            "python3", ["-m", "pytest", "-q"], root=self.root,
            settings=self.settings, confirm=lambda preview: approved.append(preview) or False,
            runner=self.runner,
        )
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(len(approved), 1)
        self.assertEqual(self.calls, [])

    def test_unknown_executable_is_rejected(self):
        result = self.execute("sh", ["-c", "echo unsafe"])
        self.assertIn("not allowlisted", result["error"])
        self.assertEqual(self.calls, [])

    def test_git_mutation_is_rejected(self):
        result = self.execute("git", ["push", "origin", "main"])
        self.assertIn("not in the os_command allowlist", result["error"])
        self.assertEqual(self.calls, [])

    def test_python_script_execution_is_rejected(self):
        result = self.execute("python3", ["dangerous.py"])
        self.assertIn("limited to approved -m modules", result["error"])
        self.assertEqual(self.calls, [])

    def test_path_traversal_is_rejected(self):
        result = self.execute("python3", ["-m", "pytest", "../outside"], cwd=".")
        self.assertIn("cannot escape", result["error"])
        self.assertEqual(self.calls, [])

    def test_cwd_traversal_is_rejected(self):
        result = self.execute("git", ["status"], cwd="../outside")
        self.assertIn("cannot escape", result["error"])
        self.assertEqual(self.calls, [])

    def test_command_line_parser_does_not_evaluate_shell_syntax(self):
        executable, args = parse_os_command_line('git status --short')
        self.assertEqual(executable, "git")
        self.assertEqual(args, ["status", "--short"])
        executable, args = parse_os_command_line('git status "; touch owned"')
        self.assertEqual(args[-1], "; touch owned")

    def test_disabled_feature_rejects_execution(self):
        settings = dict(self.settings, enabled=False)
        result = execute_os_command(
            "git", ["status"], root=self.root, settings=settings,
            confirm=lambda _: True, runner=self.runner,
        )
        self.assertIn("disabled", result["error"])
        self.assertEqual(self.calls, [])

    def test_agent_registry_exposes_os_command(self):
        from modules.engine import agent_runtime
        self.assertIn("os_command", agent_runtime.TOOL_DISPATCH)
        schemas = [item["function"]["name"] for item in agent_runtime.FUNCTIONS]
        self.assertIn("os_command", schemas)


    def test_configuration_cannot_expand_git_subcommands(self):
        self.settings["commands"]["git"]["subcommands"].append("push")
        result = self.execute("git", ["push", "origin", "main"])
        self.assertIn("not in the os_command allowlist", result["error"])
        self.assertEqual(self.calls, [])

    def test_configuration_cannot_expand_python_modules(self):
        self.settings["commands"]["python3"]["modules"].append("pip")
        result = self.execute("python3", ["-m", "pip", "install", "evil"])
        self.assertIn("limited to approved -m modules", result["error"])
        self.assertEqual(self.calls, [])


    def test_git_diff_without_summary_or_safe_path_is_rejected(self):
        result = self.execute("git", ["diff"])
        self.assertIn("must use a summary option", result["error"])
        self.assertEqual(self.calls, [])

    def test_git_show_requires_summary_mode(self):
        result = self.execute("git", ["show", "HEAD"])
        self.assertIn("limited to metadata/summary", result["error"])
        self.assertEqual(self.calls, [])

    def test_git_diff_rejects_wildcard_pathspecs(self):
        result = self.execute("git", ["diff", "--", "*"])
        self.assertIn("wildcards", result["error"])
        self.assertEqual(self.calls, [])


    def test_config_cannot_raise_hard_timeout_ceiling(self):
        self.settings["max_timeout_seconds"] = 999999
        result = self.execute("git", ["status"], timeout_seconds=999999)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.calls[0][2], 120)


    def test_git_pathspec_symlink_to_secret_is_rejected(self):
        secret = self.root / ".env.secret"
        secret.write_text("not for tools")
        link = self.root / "public-link"
        try:
            link.symlink_to(secret)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable on this platform")
        result = self.execute("git", ["diff", "--", "public-link"])
        self.assertIn("protected path", result["error"])
        self.assertEqual(self.calls, [])

    def test_git_diff_directory_pathspec_is_rejected(self):
        result = self.execute("git", ["diff", "--", "test"])
        self.assertIn("identify a file", result["error"])
        self.assertEqual(self.calls, [])

    def test_unittest_module_name_execution_is_rejected(self):
        result = self.execute("python3", ["-m", "unittest", "os.system"])
        self.assertIn("discovery mode", result["error"])
        self.assertEqual(self.calls, [])



if __name__ == "__main__":
    unittest.main()
