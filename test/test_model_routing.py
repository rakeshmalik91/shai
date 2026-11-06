import json
import pathlib
import tempfile
import unittest
from unittest import mock

from modules import model_routing


class ModelRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = pathlib.Path(self.temp.name)
        self.route_file = self.project / ".shai" / "model_routing.json"
        self.route_file.parent.mkdir()
        self.original_template = model_routing.TEMPLATE_FILE
        self.template = self.project / "template.json"
        self.template.write_text(json.dumps({
            "version": 1, "models": {role: [] for role in model_routing.ROLES}
        }), encoding="utf-8")
        model_routing.TEMPLATE_FILE = self.template
        model_routing.clear_availability_cache()

    def tearDown(self):
        model_routing.TEMPLATE_FILE = self.original_template
        model_routing.clear_availability_cache()
        self.temp.cleanup()

    def write_routes(self, models, version=1):
        self.route_file.write_text(json.dumps({
            "version": version, "models": models
        }), encoding="utf-8")

    def test_all_supported_roles_have_unconfigured_template_entries(self):
        config = model_routing.load_routing_config(self.project)
        self.assertEqual(set(config["models"]), set(model_routing.ROLES))
        self.assertTrue(all(config["models"][role] == [] for role in model_routing.ROLES))

    def test_project_routes_override_template_by_role_only(self):
        self.write_routes({"code": [
            {"provider": "ollama", "model": "qwen2.5", "priority": 1}
        ]})
        config = model_routing.load_routing_config(self.project)
        self.assertEqual(config["models"]["code"][0]["model"], "qwen2.5")
        self.assertEqual(config["models"]["chat"], [])

    def test_priority_and_availability_choose_first_available_candidate(self):
        self.write_routes({"code": [
            {"provider": "openai", "model": "later", "priority": 2},
            {"provider": "ollama", "model": "preferred", "priority": 1},
        ]})
        result = model_routing.resolve_model(
            "code", self.project, "openrouter", "default",
            provider_registry={"openai": {}, "ollama": {}},
            model_discovery=lambda provider: (
                ["preferred"] if provider == "ollama" else ["later"]
            ),
        )
        self.assertEqual((result["provider"], result["model"]), ("ollama", "preferred"))
        self.assertTrue(result["routed"])

    def test_unavailable_or_invalid_candidate_uses_default(self):
        self.write_routes({"debug": [
            {"provider": "not-a-provider", "model": "x", "priority": 1},
            {"provider": "ollama", "model": "missing", "priority": 2},
        ]})
        result = model_routing.resolve_model(
            "debug", self.project, "openrouter", "default",
            provider_registry={"ollama": {}},
            model_discovery=lambda provider: [],
        )
        self.assertEqual((result["provider"], result["model"]), ("openrouter", "default"))
        self.assertFalse(result["routed"])

    def test_unknown_role_uses_default(self):
        result = model_routing.resolve_model(
            "invented", self.project, "openrouter", "default",
            provider_registry={}, model_discovery=lambda provider: [],
        )
        self.assertEqual(result["model"], "default")
        self.assertFalse(result["routed"])

    def test_invalid_project_version_does_not_enable_routes(self):
        self.write_routes({"code": [
            {"provider": "ollama", "model": "x", "priority": 1}
        ]}, version=99)
        config = model_routing.load_routing_config(self.project)
        self.assertEqual(config["models"]["code"], [])

    def test_malformed_candidates_are_skipped_individually(self):
        self.write_routes({"code": [
            {"provider": "ollama", "model": "bad", "priority": 0},
            {"provider": "ollama", "model": "good", "priority": 2},
        ]})
        candidates = model_routing._valid_candidates(
            model_routing.load_routing_config(self.project)["models"]["code"]
        )
        self.assertEqual([c["model"] for c in candidates], ["good"])


    def test_project_routing_file_is_gitignored_and_template_is_tracked(self):
        ignore = (self.project / ".gitignore")
        ignore.write_text(".shai/*\\n!.shai/model_routing.json\\n", encoding="utf-8")
        ignore_rules = ignore.read_text(encoding="utf-8")
        self.assertIn(".shai/*", ignore_rules)
        self.assertIn("!.shai/model_routing.json", ignore_rules)
        self.assertTrue(model_routing.TEMPLATE_FILE.exists())

    def test_discovery_is_cached_per_project_and_provider(self):
        self.write_routes({"chat": [
            {"provider": "ollama", "model": "qwen", "priority": 1}
        ]})
        discover = mock.Mock(return_value=["qwen"])
        for _ in range(2):
            model_routing.resolve_model(
                "chat", self.project, "openrouter", "default",
                provider_registry={"ollama": {}}, model_discovery=discover,
            )
        self.assertEqual(discover.call_count, 1)


if __name__ == "__main__":
    unittest.main()
