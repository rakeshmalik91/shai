import json
import tempfile
import unittest
from pathlib import Path

from modules.project_paths import get_active_project_root, set_active_project_root
from modules import logging_config, model_config, provider_selection


class ActiveProjectStatePathTests(unittest.TestCase):
    def setUp(self):
        self.original_root = get_active_project_root()
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "selected-project"
        self.project.mkdir()
        self.install_root = Path(__file__).resolve().parents[1]
        self.install_state_snapshot = {}
        install_state = self.install_root / ".shai"
        if install_state.exists():
            self.install_state_snapshot = {
                item.relative_to(install_state).as_posix(): (
                    item.read_bytes() if item.is_file() else None
                )
                for item in install_state.rglob("*")
                if item.is_file()
            }

    def tearDown(self):
        set_active_project_root(self.original_root)
        self.temp.cleanup()

    def test_startup_resolver_uses_explicit_project_directory(self):
        from modules.project_paths import resolve_startup_project_root
        other = self.project / "startup-target"
        other.mkdir()
        self.assertEqual(
            resolve_startup_project_root(["agent.py", str(other)]),
            other.resolve(),
        )
        self.assertEqual(
            resolve_startup_project_root(["agent.py", "-q"]),
            Path.cwd().resolve(),
        )

    def test_model_metadata_defaults_to_selected_project(self):
        set_active_project_root(self.project)
        self.assertEqual(
            model_config.MODEL_CONFIG_FILE,
            self.project / ".shai" / "model_config.json",
        )
        model_config.update_model_metadata(
            "test-provider", "test-model", {"context_window": 12345}
        )
        expected = self.project / ".shai" / "model_config.json"
        self.assertTrue(expected.is_file())
        install_file = self.install_root / ".shai" / "model_config.json"
        self.assertEqual(
            install_file.read_bytes() if install_file.exists() else None,
            self.install_state_snapshot.get("model_config.json"),
        )
        saved = json.loads(expected.read_text(encoding="utf-8"))
        self.assertEqual(
            saved["models"]["test-provider::test-model"]["context_window"],
            12345,
        )

    def test_provider_selection_and_migration_use_selected_project(self):
        set_active_project_root(self.project)
        self.assertEqual(provider_selection._state_dir(), self.project / ".shai")
        provider_selection.save_config("test-provider", "test-model")
        expected = self.project / ".shai" / "model_routing.json"
        self.assertTrue(expected.is_file())
        saved = json.loads(expected.read_text(encoding="utf-8"))
        self.assertEqual(saved["default"], {
            "provider": "test-provider", "model": "test-model"
        })
        self.assertFalse((self.project / ".shai" / "provider_model.json").exists())
        install_file = self.install_root / ".shai" / "model_routing.json"
        self.assertEqual(
            install_file.read_bytes() if install_file.exists() else None,
            self.install_state_snapshot.get("model_routing.json"),
        )

    def test_logging_defaults_to_selected_project(self):
        set_active_project_root(self.project)
        self.assertEqual(
            logging_config.DEFAULT_LOG_DIR,
            self.project / ".shai" / "logs",
        )
        settings = logging_config.load_logging_config({"logging": {}})
        expected = self.project / ".shai" / "logs"
        self.assertEqual(settings["log_dir"], expected)
        logger = logging_config.setup_logging(per_run_file=False)
        logger.info("selected-project-log")
        self.assertTrue(expected.is_dir())
        install_state = self.install_root / ".shai"
        current_snapshot = {
            item.relative_to(install_state).as_posix(): item.read_bytes()
            for item in install_state.rglob("*") if item.is_file()
        } if install_state.exists() else {}
        self.assertEqual(current_snapshot, self.install_state_snapshot)

    def test_approval_and_project_access_files_are_scoped_to_selected_project(self):
        from modules.engine import agent_runtime
        from modules.tools import common as tool_common

        original_runtime_root = agent_runtime.ROOT
        agent_runtime.set_project_root(self.project)
        agent_runtime.save_approval_settings({
            "all": True,
            "file_edit": False,
            "file_delete": False,
            "file_truncate": False,
            "execution": False,
            "git_commit": False,
            "git_push": False,
            "tools": {},
        })
        self.assertTrue((self.project / ".shai" / "approval_config.json").is_file())
        self.assertFalse(
            (self.install_root / ".shai" / "approval_config.json").exists()
            and (self.install_root / ".shai" / "approval_config.json").read_text()
            == (self.project / ".shai" / "approval_config.json").read_text()
        )

        tool_common.configure_root(self.project)
        self.assertEqual(
            tool_common._grants_path(),
            self.project / ".shai" / "project_access.json",
        )
        agent_runtime.set_project_root(original_runtime_root)

    def test_project_switch_updates_task_state_path_and_active_root(self):
        from modules.engine import agent_runtime
        original_runtime_root = agent_runtime.ROOT
        other = self.project / "other"
        other.mkdir()
        agent_runtime.set_project_root(other)
        self.assertEqual(agent_runtime.ROOT, other)
        self.assertEqual(agent_runtime.TASK_FILE, other / ".shai" / "task.json")
        self.assertEqual(get_active_project_root(), other)
        agent_runtime.set_project_root(original_runtime_root)


if __name__ == "__main__":
    unittest.main()
