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
                "git": {
                    "enabled": True, "allow_unrestricted": False,
                    "subcommands": ["status", "diff", "log", "branch", "rev-parse", "show"],
                    "requires_approval": False,
                    "injected_global_options": ["-c", "safe.directory=${PROJECT_ROOT}", "-c", "core.fsmonitor=false", "-c", "core.pager=cat"],
                    "injected_subcommand_options": {"diff": ["--no-ext-diff", "--no-textconv"], "show": ["--no-ext-diff", "--no-textconv"]},
                    "blocked_subcommands": ["daemon", "filter-branch"],
                    "forbidden_options": ["-c", "-C", "--config-env", "--exec-path", "--git-dir", "--work-tree"],
                    "forbidden_option_prefixes": ["--config-env=", "--git-dir=", "--work-tree="],
                    "forbidden_argument_patterns": [],
                    "argument_rules": {
                        "status": {"allowed_args": ["--short", "--branch", "--porcelain", "--untracked-files=no", "--untracked-files=normal"]},
                        "diff": {"allowed_args": ["--stat", "--name-only", "--name-status", "--cached", "--staged", "--check", "--no-ext-diff", "--no-textconv", "--color=never"], "allow_project_paths_after_separator": True, "require_any": ["--stat", "--name-only", "--name-status", "--check", "<path>"], "require_any_message": "git diff must use a summary option or an explicit safe path after --."},
                        "log": {"allowed_args": ["--oneline", "--decorate", "--no-decorate", "--pretty=oneline", "--pretty=short", "--pretty=fuller", "--format=short", "--format=fuller", "--pretty=format:%h %s", "--pretty=format:%H %s", "--format=%h %s", "--format=%H %s"], "numeric_options": ["-n", "--max-count"], "max_numeric_value": 1000, "patterns": ["--pretty=format:%(?:h|H) %s", "--format=%(?:h|H) %s"], "numeric_patterns": [{"pattern": "-[1-9][0-9]{0,3}", "max": 1000}, {"pattern": "--max-count=[1-9][0-9]{0,3}", "max": 1000}]},
                        "branch": {"allowed_args": ["--show-current"]},
                        "rev-parse": {"allowed_args": ["--show-toplevel", "--is-inside-work-tree", "--short", "HEAD"]},
                        "show": {"allowed_args": ["--stat", "--oneline", "--no-patch", "HEAD"], "positional_patterns": ["HEAD~[0-9]{1,4}"], "require_any": ["--stat", "--oneline", "--no-patch"], "require_any_message": "git show is limited to metadata/summary output; add --stat, --oneline or --no-patch."}
                    }
                },
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
        self.assertIn("safe.directory={}".format(self.root), self.calls[0][0])
        self.assertEqual(self.calls[0][1], self.root)
        self.assertEqual(self.calls[0][4]["GIT_CONFIG_NOSYSTEM"], "1")

    def test_git_log_accepts_last_ten_commits(self):
        result = self.execute("git", ["log", "--oneline", "-10"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.calls[0][0][-3:], ["log", "--oneline", "-10"])

    def test_git_log_accepts_short_and_fuller_formats(self):
        for fmt in ("--format=short", "--format=fuller"):
            with self.subTest(fmt=fmt):
                self.calls.clear()
                result = self.execute("git", ["log", fmt, "-10"])
                self.assertEqual(result["status"], "completed")
                self.assertEqual(self.calls[0][0][-3:], ["log", fmt, "-10"])

    def test_git_log_accepts_safe_pretty_format(self):
        result = self.execute("git", ["log", "--pretty=format:%h %s", "-10"])
        self.assertEqual(result["status"], "completed")

    def test_git_log_rejects_unbounded_or_unsafe_options(self):
        for args in (["log", "-9999"], ["log", "--pretty=format:%H%n%an"]):
            result = self.execute("git", args)
            self.assertTrue("Unsupported git log argument" in result["error"] or "numeric argument" in result["error"])
        self.assertEqual(len(self.calls), 0)

    def test_git_diff_disables_external_diff_and_textconv(self):
        result = self.execute("git", ["diff", "--stat"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.calls[0][0][-4:], ["diff", "--no-ext-diff", "--no-textconv", "--stat"])

    def test_approval_preview_contains_complete_command(self):
        self.settings["commands"]["git"]["requires_approval"] = True
        previews = []
        execute_os_command(
            "git", ["log", "--oneline", "-10"], root=self.root,
            settings=self.settings, confirm=lambda preview: previews.append(preview) or False,
            runner=self.runner,
        )
        self.assertIn("Full command: /usr/bin/git", previews[0])
        self.assertIn("log --oneline -10", previews[0])

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
        self.assertIn("command_config.json", result["error"])
        self.assertEqual(self.calls, [])

    def test_agent_registry_exposes_os_command(self):
        from modules.engine import agent_runtime
        self.assertIn("os_command", agent_runtime.TOOL_DISPATCH)
        schemas = [item["function"]["name"] for item in agent_runtime.FUNCTIONS]
        self.assertIn("os_command", schemas)


    def test_exact_command_whitelist_bypasses_positive_argument_rules(self):
        self.settings["commands"]["git"]["approved_commands"] = [["log", "--format=custom", "-10"]]
        result = self.execute("git", ["log", "--format=custom", "-10"])
        self.assertEqual(result["status"], "completed")

    def test_subcommand_whitelist_bypasses_positive_argument_rules(self):
        self.settings["commands"]["git"]["approved_subcommands"] = ["log"]
        result = self.execute("git", ["log", "--format=custom", "-10"])
        self.assertEqual(result["status"], "completed")

    def test_blacklist_cannot_be_overridden_by_whitelist(self):
        profile = self.settings["commands"]["git"]
        profile["approved_commands"] = [["daemon", "--reuseaddr"]]
        profile["approved_subcommands"] = ["daemon"]
        self.settings["commands"]["git"]["allow_unrestricted"] = True
        result = self.execute("git", ["daemon", "--reuseaddr"])
        self.assertIn("subcommand is blocked", result["error"])
        self.assertEqual(self.calls, [])

    def test_configuration_can_enable_unrestricted_git_commands(self):
        self.settings["commands"]["git"]["allow_unrestricted"] = True
        result = self.execute("git", ["push", "origin", "main"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.calls[0][0][-3:], ["push", "origin", "main"])

    def test_restricted_git_policy_is_configuration_driven(self):
        self.settings["commands"]["git"]["subcommands"].append("tag")
        self.settings["commands"]["git"]["argument_rules"]["tag"] = {"allowed_args": ["--list"]}
        result = self.execute("git", ["tag", "--list"])
        self.assertEqual(result["status"], "completed")

    def test_unrestricted_git_allows_unlisted_subcommands_and_options(self):
        self.settings["commands"]["git"]["allow_unrestricted"] = True
        args = ["maintenance", "run", "--task=gc"]
        result = self.execute("git", args)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.calls[0][0][-len(args):], args)

    def test_unrestricted_git_still_blocks_forbidden_options(self):
        self.settings["commands"]["git"]["allow_unrestricted"] = True
        for args in (["-c", "user.name=Test", "status"],
                     ["status", "--git-dir=/tmp/other"]):
            result = self.execute("git", list(args))
            self.assertIn("blocked", result["error"])
        self.assertEqual(self.calls, [])

    def test_unrestricted_git_still_blocks_blacklisted_subcommands(self):
        self.settings["commands"]["git"]["allow_unrestricted"] = True
        result = self.execute("git", ["daemon", "--reuseaddr"])
        self.assertIn("subcommand is blocked", result["error"])
        self.assertEqual(self.calls, [])

    def test_unrestricted_git_still_applies_forbidden_argument_patterns(self):
        self.settings["commands"]["git"]["allow_unrestricted"] = True
        self.settings["commands"]["git"]["forbidden_argument_patterns"] = [r"^--upload-pack="]
        result = self.execute("git", ["upload-archive", "--upload-pack=/tmp/evil"])
        self.assertIn("blocked pattern", result["error"])
        self.assertEqual(self.calls, [])

    def test_configuration_cannot_expand_python_modules(self):
        self.settings["commands"]["python3"]["modules"].append("pip")
        result = self.execute("python3", ["-m", "pip", "install", "evil"])
        self.assertIn("limited to approved -m modules", result["error"])
        self.assertEqual(self.calls, [])


    def test_git_diff_without_summary_or_safe_path_is_rejected(self):
        result = self.execute("git", ["diff"])
        self.assertIn("git diff must use a summary option", result["error"])
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
