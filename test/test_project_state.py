import json
import tempfile
import unittest
from pathlib import Path

from modules.project_state import ensure_project_state, migrate_legacy_model_selection


class ProjectStateTests(unittest.TestCase):
    def test_ignore_file_ignores_only_managed_runtime_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            state = ensure_project_state(temp)
            rules = (state / ".gitignore").read_text(encoding="utf-8").splitlines()
            self.assertEqual(rules, [
                "# BEGIN SHAI MANAGED IGNORE RULES",
                "/logs/", "/shai_backups/", "/task.json", "/history.json",
                "# END SHAI MANAGED IGNORE RULES",
            ])
            for allowed in ("model_config.json",
                            "approval_config.json", "os_command_approvals.json",
                            "model_routing.json", "project_access.json"):
                self.assertFalse(any(rule == allowed or rule == ".shai" for rule in rules))

    def test_ignore_file_preserves_user_rules_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / ".shai"
            state.mkdir()
            ignore = state / ".gitignore"
            ignore.write_text(chr(10).join(["# user rule", "custom.tmp", ""]), encoding="utf-8")
            ensure_project_state(temp)
            first = ignore.read_text(encoding="utf-8")
            ensure_project_state(temp)
            self.assertEqual(ignore.read_text(encoding="utf-8"), first)
            self.assertIn("custom.tmp", first)
            self.assertEqual(first.count("BEGIN SHAI MANAGED"), 1)

    def test_legacy_selection_migrates_into_existing_routing_file_and_is_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            assets = project / "assets"
            assets.mkdir()
            core = assets / "shai_config.json"
            core.write_text(json.dumps({"max_tool_call_iterations": 12}), encoding="utf-8")
            state = project / ".shai"
            state.mkdir()
            legacy = state / "provider_model.json"
            legacy.write_text(json.dumps({
                "provider": "openai", "model": "gpt-legacy"
            }), encoding="utf-8")
            routing_file = state / "model_routing.json"
            routing_file.write_text(json.dumps({
                "version": 1,
                "default": {"provider": "ollama", "model": "local-model"},
                "models": {"code": [
                    {"provider": "ollama", "model": "qwen-code", "priority": 1}
                ]},
            }), encoding="utf-8")

            migrate_legacy_model_selection(project, core)
            saved = json.loads(routing_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["default"], {
                "provider": "ollama", "model": "local-model"
            })
            self.assertEqual(saved["models"]["code"][0]["model"], "qwen-code")
            self.assertFalse(legacy.exists())

    def test_provider_and_model_state_migrates_out_of_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            assets = project / "assets"
            assets.mkdir()
            core = assets / "shai_config.json"
            core.write_text(json.dumps({
                "provider": "kilo-free", "model": "vendor/model:free",
                "max_tool_call_iterations": 20,
            }), encoding="utf-8")
            old_models = assets / "model_config.json"
            old_models.write_text(json.dumps({"version": 1, "models": {
                "kilo-free::vendor/model:free": {"context_window": 128000}
            }}), encoding="utf-8")

            state = migrate_legacy_model_selection(project, core)
            selected = json.loads((state / "model_routing.json").read_text())["default"]
            core_after = json.loads(core.read_text())
            models_after = json.loads((state / "model_config.json").read_text())

            self.assertEqual(selected, {
                "provider": "kilo-free", "model": "vendor/model:free"
            })
            self.assertFalse((state / "provider_model.json").exists())
            self.assertEqual(core_after, {"max_tool_call_iterations": 20})
            self.assertFalse(old_models.exists())
            self.assertEqual(
                models_after["models"]["kilo-free::vendor/model:free"]["context_window"],
                128000,
            )


if __name__ == "__main__":
    unittest.main()
