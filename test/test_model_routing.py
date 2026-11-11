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
            "version": 1,
            "default": {"provider": "openrouter", "model": "shared-default"},
            "models": {role: [] for role in model_routing.ROLES},
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

    def test_default_provider_model_loads_from_assets_and_project_override(self):
        shared = model_routing.load_default_model(self.project)
        self.assertEqual(shared, {
            "provider": "openrouter", "model": "shared-default"
        })
        self.route_file.write_text(json.dumps({
            "version": 1,
            "default": {"provider": "ollama", "model": "qwen-local"},
            "models": {},
        }), encoding="utf-8")
        selected = model_routing.load_default_model(self.project)
        self.assertEqual(selected, {
            "provider": "ollama", "model": "qwen-local"
        })

    def test_incomplete_project_default_does_not_mix_with_shared_default(self):
        self.route_file.write_text(json.dumps({
            "version": 1,
            "default": {"provider": "ollama"},
        }), encoding="utf-8")
        self.assertEqual(
            model_routing.load_default_model(self.project),
            {"provider": "openrouter", "model": "shared-default"},
        )

    def test_saving_default_preserves_existing_task_routes(self):
        self.write_routes({"code": [
            {"provider": "ollama", "model": "qwen-code", "priority": 1}
        ]})
        model_routing.save_default_model(self.project, "gemini", "gemini-flash")
        saved = json.loads(self.route_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["default"], {
            "provider": "gemini", "model": "gemini-flash"
        })
        self.assertEqual(saved["models"]["code"][0]["model"], "qwen-code")
        self.assertFalse((self.route_file.parent / "provider_model.json").exists())

    def test_project_routes_override_template_by_role_only(self):
        self.write_routes({"code": [
            {"provider": "ollama", "model": "qwen2.5", "priority": 1}
        ]})
        config = model_routing.load_routing_config(self.project)
        self.assertEqual(config["models"]["code"][0]["model"], "qwen2.5")
        self.assertEqual(config["models"]["chat"], [])

    def test_priority_route_does_not_probe_model_catalogue(self):
        self.write_routes({"code": [
            {"provider": "openai", "model": "later", "priority": 2},
            {"provider": "ollama", "model": "preferred", "priority": 1},
        ]})
        discover = mock.Mock(side_effect=AssertionError("catalogue must not be queried"))
        result = model_routing.resolve_model(
            "code", self.project, "openrouter", "default",
            provider_registry={"openai": {}, "ollama": {}},
            model_discovery=discover,
        )
        self.assertEqual((result["provider"], result["model"]), ("ollama", "preferred"))
        self.assertTrue(result["routed"])
        discover.assert_not_called()

    def test_unknown_provider_is_skipped_but_configured_model_is_trusted(self):
        self.write_routes({"debug": [
            {"provider": "not-a-provider", "model": "x", "priority": 1},
            {"provider": "ollama", "model": "configured-even-if-not-in-catalogue", "priority": 2},
        ]})
        discover = mock.Mock(side_effect=AssertionError("catalogue must not be queried"))
        result = model_routing.resolve_model(
            "debug", self.project, "openrouter", "default",
            provider_registry={"ollama": {}}, model_discovery=discover,
        )
        self.assertEqual((result["provider"], result["model"]),
                         ("ollama", "configured-even-if-not-in-catalogue"))
        self.assertTrue(result["routed"])
        discover.assert_not_called()


    def test_routes_include_active_default_after_inactive_candidates(self):
        self.write_routes({"code": [
            {"provider": "openai", "model": "retired-model", "priority": 1},
            {"provider": "ollama", "model": "working-model", "priority": 2},
        ]})
        routes = model_routing.resolve_model_candidates(
            "code", self.project, "openrouter", "active-model",
            provider_registry={"openai": {}, "ollama": {}, "openrouter": {}},
        )
        self.assertEqual(
            [(r["provider"], r["model"], r["routed"]) for r in routes],
            [("openai", "retired-model", True),
             ("ollama", "working-model", True),
             ("openrouter", "active-model", False)],
        )

    def test_discontinued_inactive_route_is_silently_skipped(self):
        routes = [
            {"provider": "openai", "model": "retired", "role": "code", "routed": True},
            {"provider": "ollama", "model": "working", "role": "code", "routed": True},
            {"provider": "openrouter", "model": "active", "role": "code", "routed": False},
        ]
        attempted = []

        def request(route):
            attempted.append(route["model"])
            if route["model"] == "retired":
                raise RuntimeError("API error 404: model_not_found: retired")
            return route["model"]

        result = model_routing.request_with_model_fallback(routes, request)
        self.assertEqual(result, "working")
        self.assertEqual(attempted, ["retired", "working"])

    def test_active_model_unavailability_is_not_suppressed(self):
        routes = [
            {"provider": "openrouter", "model": "active", "role": "code", "routed": False},
        ]
        with self.assertRaisesRegex(RuntimeError, "model_not_found"):
            model_routing.request_with_model_fallback(
                routes,
                lambda route: (_ for _ in ()).throw(
                    RuntimeError("API error 404: model_not_found: active")
                ),
            )

    def test_non_model_errors_do_not_trigger_route_fallback(self):
        routes = [
            {"provider": "openai", "model": "candidate", "role": "code", "routed": True},
            {"provider": "openrouter", "model": "active", "role": "code", "routed": False},
        ]
        with self.assertRaisesRegex(RuntimeError, "rate limit"):
            model_routing.request_with_model_fallback(
                routes, lambda route: (_ for _ in ()).throw(RuntimeError("rate limit exceeded"))
            )

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

    def test_candidate_validation_rejects_booleans_and_missing_keys(self):
        items = [
            {"provider": "ollama", "model": "test", "priority": True},  # boolean priority
            {"provider": "", "model": "test", "priority": 1},           # empty provider
            {"provider": "ollama", "model": "", "priority": 1},           # empty model
            {"model": "test", "priority": 1},                            # missing provider
            {"provider": "ollama", "priority": 1},                        # missing model
            "not_a_dict",                                                 # non-dict
            {"provider": "ollama", "model": "valid", "priority": 1},     # valid
        ]
        valid = model_routing._valid_candidates(items)
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0]["model"], "valid")

    def test_candidate_deduplication_preserves_first_occurrence(self):
        items = [
            {"provider": "ollama", "model": "qwen", "priority": 1},
            {"provider": "ollama", "model": "qwen", "priority": 2},
        ]
        valid = model_routing._valid_candidates(items)
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0]["priority"], 1)

    def test_project_routing_file_is_gitignored_and_template_is_tracked(self):
        ignore = (self.project / ".gitignore")
        ignore.write_text(".shai/*\n!.shai/model_routing.json\n", encoding="utf-8")
        ignore_rules = ignore.read_text(encoding="utf-8")
        self.assertIn(".shai/*", ignore_rules)
        self.assertIn("!.shai/model_routing.json", ignore_rules)
        self.assertTrue(model_routing.TEMPLATE_FILE.exists())

    def test_clear_availability_cache_is_backward_compatible_noop(self):
        self.assertIsNone(model_routing.clear_availability_cache())


if __name__ == "__main__":
    unittest.main()
