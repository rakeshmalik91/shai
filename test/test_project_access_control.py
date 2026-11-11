import json
import pathlib
import tempfile
import unittest

from modules.tools import common
from modules.tools import filesystem


class ProjectAccessControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "project"
        self.root.mkdir()
        self.config = pathlib.Path(self.tmp.name) / "access.json"
        self.original_root = common.ROOT
        self.original_config = common.GENERIC_CONFIG_FILE
        common.configure_root(self.root)
        common.configure_access_config(self.config)
        (self.root / "visible").mkdir()
        (self.root / "hidden").mkdir()
        (self.root / "visible" / "ok.txt").write_text("visible needle\\n")
        (self.root / "hidden" / "secret.txt").write_text("hidden needle\\n")
        self.write_policy({
            "default": {"view": True, "edit": True, "truncate": False,
                        "delete": False, "execute": True},
            "paths": [
                {"path": "hidden/**", "view": False},
                {"path": "visible/readonly.txt", "edit": False},
                {"path": "visible/truncate.txt", "truncate": False}
            ]
        })

    def tearDown(self):
        common.configure_root(self.original_root)
        common.configure_access_config(self.original_config)
        self.tmp.cleanup()

    def write_policy(self, policy):
        self.config.write_text(json.dumps({"project_access": policy}), encoding="utf-8")

    def test_generic_policy_uses_dedicated_config_file(self):
        expected = pathlib.Path(common.__file__).resolve().parents[2] / "assets" / "project_access_config.json"
        self.assertEqual(
            pathlib.Path(self.original_config).name,
            "project_access_config.json"
        )
        self.assertTrue(expected.is_file())
        core_config = json.loads((expected.parent / "shai_config.json").read_text(encoding="utf-8"))
        self.assertNotIn("project_access", core_config)

    def test_default_policy_and_rule_deny(self):
        self.assertTrue(common.access_decision("visible/ok.txt", "view")[0])
        self.assertFalse(common.access_decision("hidden/secret.txt", "view")[0])

    def test_explicit_deny_overrides_broad_allow(self):
        self.write_policy({
            "default": {"view": True, "edit": True, "truncate": False,
                        "delete": False, "execute": True},
            "paths": [
                {"path": "hidden/**", "view": True},
                {"path": "hidden/secret.txt", "view": False}
            ]
        })
        self.assertFalse(common.access_decision("hidden/secret.txt", "view")[0])

    def test_read_denied(self):
        result = filesystem.read_file("hidden/secret.txt")
        self.assertIn("Access denied", result)
        self.assertNotIn("hidden needle", result)

    def test_listing_hides_denied_descendants(self):
        result = filesystem.list_files(".")
        self.assertIn("visible/", result)
        self.assertNotIn("hidden/", result)
        self.assertNotIn("secret.txt", result)

    def test_search_prunes_denied_directories(self):
        result = filesystem.search_files("needle")
        self.assertIn("visible/ok.txt", result)
        self.assertNotIn("hidden/secret.txt", result)

    def test_symlink_escape_is_rejected(self):
        outside = pathlib.Path(self.tmp.name) / "outside.txt"
        outside.write_text("private")
        try:
            (self.root / "escape.txt").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks are unavailable")
        with self.assertRaises((ValueError, common.ProjectAccessDenied)):
            common.safe_path("escape.txt")
        self.assertFalse(filesystem.list_files("." ).find("escape.txt") >= 0)

    def test_absolute_and_parent_paths_are_rejected(self):
        for value in ("../outside", str(self.root / "visible/ok.txt")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    common.safe_path(value)

    def test_permission_validation_and_diagnostic(self):
        with self.assertRaises(common.ProjectAccessDenied) as caught:
            common.require_access("visible/readonly.txt", "edit")
        self.assertIn("edit", str(caught.exception))
        self.assertIn("visible/readonly.txt", str(caught.exception))

    def test_invalid_config_falls_back_to_safe_defaults(self):
        self.config.write_text("{ invalid", encoding="utf-8")
        policy = common.load_access_policy()
        self.assertFalse(policy["default"]["truncate"])
        self.assertFalse(policy["default"]["delete"])


    def test_execute_permission_is_enforced_by_os_command(self):
        from modules.tools.os_command import execute_os_command
        self.write_policy({
            "default": {"view": True, "edit": True, "truncate": False,
                        "delete": False, "execute": True},
            "paths": [{"path": "blocked/**", "execute": False}]
        })
        (self.root / "blocked").mkdir()
        result = execute_os_command(
            "python3", ["-m", "pytest", "-q"], cwd="blocked",
            root=self.root,
            settings={"enabled": True, "commands": {
                "python3": {"enabled": True, "modules": ["pytest"], "requires_approval": False}
            }},
            confirm=lambda _preview: True,
            runner=lambda *_args, **_kwargs: self.fail("runner must not be called")
        )
        self.assertIn("access denied", result.get("error", "").lower())

    def test_edit_and_truncate_are_independent_permissions(self):
        self.write_policy({
            "default": {"view": True, "edit": True, "truncate": False,
                        "delete": False, "execute": True},
            "paths": [{"path": "visible/readonly.txt", "edit": False}]
        })
        with self.assertRaises(common.ProjectAccessDenied):
            common.require_access("visible/readonly.txt", "edit")
        with self.assertRaises(common.ProjectAccessDenied):
            common.require_access("visible/ok.txt", "truncate")
        self.assertTrue(common.require_access("visible/ok.txt", "edit"))

    def test_glob_rule_applies_to_directory_and_descendants(self):
        self.write_policy({
            "default": {"view": True, "edit": True, "truncate": False,
                        "delete": False, "execute": True},
            "paths": [{"path": "visible/**", "view": False}]
        })
        self.assertFalse(common.access_decision("visible", "view")[0])
        self.assertFalse(common.access_decision("visible/ok.txt", "view")[0])


    def test_single_star_does_not_cross_directory_boundary(self):
        self.write_policy({
            "default": {"view": True, "edit": True, "truncate": False,
                        "delete": False, "execute": True},
            "paths": [{"path": "visible/*", "view": False}]
        })
        self.assertFalse(common.access_decision("visible/ok.txt", "view")[0])
        self.assertTrue(common.access_decision("visible/deeper/file.txt", "view")[0])

    def test_unknown_permission_rejected(self):
        with self.assertRaises(ValueError):
            common.access_decision("visible/ok.txt", "admin")

    def test_interactive_grant_for_exact_file_and_requested_capability(self):
        from unittest.mock import patch
        answers = iter(["1", "r"])
        with patch("sys.stdin.isatty", return_value=True), patch.object(
            common, "ACCESS_PROMPT", side_effect=lambda _prompt: next(answers)
        ):
            self.assertTrue(common.require_access("hidden/secret.txt", "view"))
        self.assertTrue(common.access_decision("hidden/secret.txt", "view")[0])
        self.assertFalse(common.access_decision("hidden/secret.txt", "edit")[0])
        self.assertTrue((self.root / ".shai" / "project_access.json").exists())

    def test_interactive_parent_grant_and_write_permissions(self):
        from unittest.mock import patch
        self.write_policy({
            "default": {"view": True, "edit": True, "truncate": False,
                        "delete": False, "execute": True},
            "paths": [{"path": "hidden/**", "edit": False, "truncate": False}]
        })
        answers = iter(["2", "w"])
        with patch("sys.stdin.isatty", return_value=True), patch.object(
            common, "ACCESS_PROMPT", side_effect=lambda _prompt: next(answers)
        ):
            self.assertTrue(common.require_access("hidden/secret.txt", "edit"))
        self.assertTrue(common.access_decision("hidden/another.txt", "edit")[0])
        self.assertTrue(common.access_decision("hidden/another.txt", "truncate")[0])
        self.assertFalse(common.access_decision("hidden/another.txt", "view")[0])

    def test_interactive_level_two_parent_grant(self):
        from unittest.mock import patch
        (self.root / "area" / "nested").mkdir(parents=True)
        answers = iter(["3", "x"])
        with patch("sys.stdin.isatty", return_value=True), patch.object(
            common, "ACCESS_PROMPT", side_effect=lambda _prompt: next(answers)
        ):
            self.assertTrue(common.require_access("area/nested/task.py", "execute"))
        self.assertTrue(common.access_decision("area/other/task.py", "execute")[0])

    def test_interactive_deny_does_not_persist(self):
        from unittest.mock import patch
        answers = iter(["4"])
        with patch("sys.stdin.isatty", return_value=True), patch.object(
            common, "ACCESS_PROMPT", side_effect=lambda _prompt: next(answers)
        ):
            with self.assertRaises(common.ProjectAccessDenied):
                common.require_access("hidden/secret.txt", "view")
        self.assertFalse((self.root / ".shai" / "project_access.json").exists())

    def test_test_runner_never_falls_through_to_stdin(self):
        from unittest.mock import patch
        with patch("sys.stdin.isatty", return_value=True), patch.object(
            common, "ACCESS_PROMPT", None
        ), patch("builtins.input", side_effect=AssertionError("test attempted stdin prompt")):
            with self.assertRaises(common.ProjectAccessDenied):
                common.require_access("hidden/secret.txt", "view")

    def test_compact_rwx_permissions_and_delete_bit(self):
        from unittest.mock import patch
        answers = iter(["1", "rwx"])
        with patch("sys.stdin.isatty", return_value=True), patch.object(
            common, "ACCESS_PROMPT", side_effect=lambda _prompt: next(answers)
        ):
            self.assertTrue(common.require_access("hidden/secret.txt", "view"))
        for permission in ("view", "edit", "truncate", "execute"):
            self.assertTrue(common.access_decision("hidden/secret.txt", permission)[0])
        self.assertFalse(common.access_decision("hidden/secret.txt", "delete")[0])

    def test_compact_delete_bit_is_independent(self):
        from unittest.mock import patch
        answers = iter(["1", "d"])
        with patch("sys.stdin.isatty", return_value=True), patch.object(
            common, "ACCESS_PROMPT", side_effect=lambda _prompt: next(answers)
        ):
            self.assertTrue(common.require_access("hidden/secret.txt", "delete"))
        self.assertTrue(common.access_decision("hidden/secret.txt", "delete")[0])
        self.assertFalse(common.access_decision("hidden/secret.txt", "view")[0])

    def test_non_interactive_denial_never_prompts(self):
        from unittest.mock import patch
        with patch("sys.stdin.isatty", return_value=False), patch.object(
            common, "ACCESS_PROMPT", side_effect=AssertionError("must not prompt")
        ):
            with self.assertRaises(common.ProjectAccessDenied):
                common.require_access("hidden/secret.txt", "view")


    def test_generic_explicit_deny_wins_even_when_broader_allow_is_later(self):
        self.write_policy({
            "default": {"view": False, "edit": True, "truncate": False,
                        "delete": False, "execute": True},
            "paths": [
                {"path": "hidden/secret.txt", "view": False},
                {"path": "hidden/**", "view": True}
            ]
        })
        self.assertFalse(common.access_decision("hidden/secret.txt", "view")[0])

    def test_all_grant_authorizes_every_supported_capability(self):
        from unittest.mock import patch
        self.write_policy({
            "default": {"view": False, "edit": False, "truncate": False,
                        "delete": False, "execute": False},
            "paths": []
        })
        answers = iter(["1", "a"])
        with patch("sys.stdin.isatty", return_value=True), patch.object(
            common, "ACCESS_PROMPT", side_effect=lambda _prompt: next(answers)
        ):
            self.assertTrue(common.require_access("visible/ok.txt", "view"))
        for permission in ("view", "edit", "truncate", "delete", "execute"):
            self.assertTrue(common.access_decision("visible/ok.txt", permission)[0])

    def test_project_grants_are_isolated_per_project(self):
        from unittest.mock import patch
        answers = iter(["1", "r"])
        with patch("sys.stdin.isatty", return_value=True), patch.object(
            common, "ACCESS_PROMPT", side_effect=lambda _prompt: next(answers)
        ):
            common.require_access("hidden/secret.txt", "view")
        other_root = pathlib.Path(self.tmp.name) / "other-project"
        other_root.mkdir()
        common.configure_root(other_root)
        self.assertFalse(common.access_decision("hidden/secret.txt", "view")[0])


    def test_exact_folder_grant_covers_descendants(self):
        from unittest.mock import patch
        answers = iter(["1", "r"])
        with patch("sys.stdin.isatty", return_value=True), patch.object(
            common, "ACCESS_PROMPT", side_effect=lambda _prompt: next(answers)
        ):
            self.assertTrue(common.require_access("hidden", "view"))
        self.assertTrue(common.access_decision("hidden/secret.txt", "view")[0])


    def test_level_two_parent_at_project_root_covers_project(self):
        from unittest.mock import patch
        answers = iter(["3", "r"])
        with patch("sys.stdin.isatty", return_value=True), patch.object(
            common, "ACCESS_PROMPT", side_effect=lambda _prompt: next(answers)
        ):
            self.assertTrue(common.require_access("hidden/secret.txt", "view"))
        self.assertTrue(common.access_decision("visible/ok.txt", "view")[0])

    def test_agent_runtime_read_and_explicit_folder_access_are_gated(self):
        from modules.engine import agent_runtime as runtime
        original_runtime_root = runtime.ROOT
        runtime.ROOT = self.root
        try:
            with self.assertRaises(common.ProjectAccessDenied):
                runtime.read_file("hidden/secret.txt")
            with self.assertRaises(common.ProjectAccessDenied):
                runtime.list_files("hidden")
        finally:
            runtime.ROOT = original_runtime_root

    def test_agent_runtime_recursive_listing_prunes_denied_directories(self):
        from modules.engine import agent_runtime as runtime
        original_runtime_root = runtime.ROOT
        runtime.ROOT = self.root
        try:
            result = runtime.list_files(".")
        finally:
            runtime.ROOT = original_runtime_root
        self.assertIn("visible/ok.txt", result)
        self.assertNotIn("hidden/secret.txt", result)


    def test_agent_runtime_prompts_and_persists_on_explicit_block(self):
        from unittest.mock import patch
        from modules.engine import agent_runtime as runtime
        original_runtime_root = runtime.ROOT
        # Importing agent_runtime initializes the shared tools root from the
        # real project. Re-apply this test's temporary root after that import
        # so path checks and persisted grants use the same project directory.
        common.configure_root(self.root)
        runtime.ROOT = self.root
        answers = iter(["1", "r"])
        try:
            with patch("sys.stdin.isatty", return_value=True), patch.object(
                common, "ACCESS_PROMPT", side_effect=lambda _prompt: next(answers)
            ):
                result = runtime.read_file("hidden/secret.txt")
        finally:
            runtime.ROOT = original_runtime_root
        self.assertIn("hidden needle", result)
        self.assertTrue((self.root / ".shai" / "project_access.json").exists())

    def test_os_command_requires_execute_for_pytest_target(self):
        from modules.tools.os_command import execute_os_command
        (self.root / "blocked").mkdir()
        (self.root / "blocked" / "test_sample.py").write_text("def test_x(): pass\n")
        self.write_policy({
            "default": {"view": True, "edit": True, "truncate": False,
                        "delete": False, "execute": True},
            "paths": [{"path": "blocked/**", "execute": False}]
        })
        result = execute_os_command(
            "python3", ["-m", "pytest", "blocked/test_sample.py"], cwd=".",
            root=self.root,
            settings={"enabled": True, "commands": {
                "python3": {"enabled": True, "modules": ["pytest"], "requires_approval": False}
            }},
            confirm=lambda _preview: True,
            runner=lambda *_args, **_kwargs: self.fail("runner must not be called")
        )
        self.assertIn("access denied", result.get("error", "").lower())


    def test_existing_file_write_also_requires_read_for_safe_diff(self):
        from modules.engine import agent_runtime as runtime
        original_runtime_root = runtime.ROOT
        runtime.ROOT = self.root
        self.write_policy({
            "default": {"view": False, "edit": True, "truncate": True,
                        "delete": False, "execute": True},
            "paths": []
        })
        try:
            with self.assertRaises(common.ProjectAccessDenied):
                runtime.edit_file("visible/ok.txt", "updated\n")
        finally:
            runtime.ROOT = original_runtime_root
        self.assertEqual((self.root / "visible" / "ok.txt").read_text(), "visible needle\\n")


    def test_grant_without_requested_capability_is_not_persisted(self):
        from unittest.mock import patch
        answers = iter(["1", "w"])
        with patch("sys.stdin.isatty", return_value=True), patch.object(
            common, "ACCESS_PROMPT", side_effect=lambda _prompt: next(answers)
        ):
            with self.assertRaises(common.ProjectAccessDenied):
                common.require_access("hidden/secret.txt", "view")
        self.assertFalse((self.root / ".shai" / "project_access.json").exists())



if __name__ == "__main__":
    unittest.main()
