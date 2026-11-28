import json
import pathlib
import tempfile
import unittest

from modules.model_config import (
    DEFAULT_MODEL_ATTRIBUTES, get_model_config, load_model_config,
    migrate_legacy_config, normalize_attributes, save_model_config,
    update_model_metadata,
)


class ModelConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.temp.name) / "model_config.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_new_config_has_broad_defaults_and_sections(self):
        config = load_model_config(self.path, migrate=False)
        self.assertEqual(set(config), {"version", "defaults", "models", "overrides"})
        self.assertEqual(config["defaults"]["supported_input_types"], ["text"])
        self.assertIsNone(config["defaults"]["supports_tools"])

    def test_model_values_override_defaults_and_user_overrides_win(self):
        config = {
            "version": 1,
            "defaults": {"supports_tools": False, "supported_input_types": ["text"]},
            "models": {"p::m": {"context_window": 1000, "supports_tools": True}},
            "overrides": {"p::m": {"context_window": 2000}},
        }
        resolved = get_model_config("p", "m", config)
        self.assertEqual(resolved["context_window"], 2000)
        self.assertTrue(resolved["supports_tools"])
        self.assertEqual(resolved["supported_input_types"], ["text"])

    def test_unknown_attributes_are_preserved_for_future_extensions(self):
        attrs = normalize_attributes({"context_window": 4096, "vendor_feature": {"x": 1}})
        self.assertEqual(attrs["vendor_feature"], {"x": 1})

    def test_invalid_limits_and_boolean_limits_are_rejected(self):
        attrs = normalize_attributes({"context_window": True, "max_output_tokens": -1})
        self.assertIsNone(attrs["context_window"])
        self.assertIsNone(attrs["max_output_tokens"])

    def test_content_types_are_deduplicated_and_invalid_values_removed(self):
        attrs = normalize_attributes({"supported_input_types": ["text", " image ", "text", 7, ""]})
        self.assertEqual(attrs["supported_input_types"], ["text", "image"])

    def test_legacy_context_config_migrates_models_and_overrides(self):
        migrated = migrate_legacy_config({
            "model_context_windows": {"p::m": 32000},
            "model_context_window_overrides": {"p::m": 64000},
            "context_window_tokens": 128000, "provider": "q", "model": "n",
        })
        self.assertEqual(migrated["models"]["p::m"]["context_window"], 32000)
        self.assertEqual(migrated["overrides"]["p::m"]["context_window"], 64000)
        self.assertEqual(migrated["models"]["q::n"]["context_window"], 128000)

    def test_legacy_file_is_migrated_when_new_file_is_missing(self):
        legacy = self.path.with_name("context_window_config.json")
        legacy.write_text(json.dumps({"model_context_windows": {"p::m": 777}}))
        config = load_model_config(self.path)
        self.assertEqual(config["models"]["p::m"]["context_window"], 777)
        self.assertTrue(self.path.exists())

    def test_provider_update_does_not_overwrite_user_override(self):
        save_model_config({"version": 1,
            "models": {"p::m": {"context_window": 1000}},
            "overrides": {"p::m": {"context_window": 2000}}}, self.path)
        update_model_metadata("p", "m", {"context_window": 4000, "supports_tools": True}, self.path)
        resolved = get_model_config("p", "m", load_model_config(self.path))
        self.assertEqual(resolved["context_window"], 2000)
        self.assertTrue(resolved["supports_tools"])

    def test_model_and_override_entries_are_sorted_when_saved(self):
        save_model_config({
            "version": 1,
            "models": {
                "z-provider::z-model": {"context_window": 1},
                "a-provider::b-model": {"context_window": 2},
                "a-provider::a-model": {"context_window": 3},
            },
            "overrides": {
                "z-provider::model": {"context_window": 4},
                "a-provider::model": {"context_window": 5},
            },
        }, self.path)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(list(raw["models"]), [
            "a-provider::a-model", "a-provider::b-model", "z-provider::z-model"
        ])
        self.assertEqual(list(raw["overrides"]), [
            "a-provider::model", "z-provider::model"
        ])

    def test_round_trip_preserves_additional_attributes(self):
        save_model_config({"version": 1, "models": {
            "p::m": {"context_window": 100, "provider_extra": "value"}
        }}, self.path)
        self.assertEqual(load_model_config(self.path)["models"]["p::m"]["provider_extra"], "value")


if __name__ == "__main__":
    unittest.main()
