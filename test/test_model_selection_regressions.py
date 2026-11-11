"""Regression coverage for provider selection and model capability persistence."""
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from modules import provider_selection as selection
from modules.model_config import load_model_config, get_model_config


class ModelSelectionRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_config_file = selection.CONFIG_FILE
        selection.CONFIG_FILE = pathlib.Path(self.temp.name) / "assets" / "shai_config.json"
        selection.CONFIG_FILE.parent.mkdir(parents=True)
        selection.MODEL_CATALOGUE_ITEMS.clear()
        selection.MODEL_CONTEXT_WINDOWS.clear()

    def tearDown(self):
        selection.CONFIG_FILE = self.original_config_file
        selection.MODEL_CATALOGUE_ITEMS.clear()
        selection.MODEL_CONTEXT_WINDOWS.clear()
        self.temp.cleanup()

    def test_selected_detail_fetch_uses_only_selected_model_and_timeout(self):
        selection.CONFIG_FILE.write_text(json.dumps({
            "providers": {"openai": {
                "model_detail_url": "https://example.test/models/{model}",
                "key_env": "OPENAI_API_KEY",
            }}
        }), encoding="utf-8")
        payload = {"data": {"id": "selected/model", "context_length": 64000,
                            "max_output_tokens": 8000, "tool_calling": True}}
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "secret"}), \
             mock.patch.object(selection, "_get_json", return_value=payload) as fetch:
            metadata = selection.get_selected_model_details("openai", "selected/model", timeout=5)
        self.assertEqual(metadata["context_window"], 64000)
        self.assertEqual(metadata["max_output_tokens"], 8000)
        self.assertTrue(metadata["supports_tools"])
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.args[0], "https://example.test/models/selected/model")
        self.assertEqual(fetch.call_args.kwargs["timeout"], 5)

    def test_selected_detail_encodes_model_path_and_does_not_fetch_catalogue(self):
        selection.CONFIG_FILE.write_text(json.dumps({
            "providers": {"openai": {
                "model_detail_url": "https://example.test/models/{model}"
            }}
        }), encoding="utf-8")
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), \
             mock.patch.object(selection, "_get_json", return_value={
            "id": "vendor/model name", "context_length": 32000
        }) as fetch:
            selection.get_selected_model_details("openai", "vendor/model name")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.args[0],
                         "https://example.test/models/vendor/model%20name")

    def test_gemini_detail_uses_query_auth_once_and_extracts_limits(self):
        selection.CONFIG_FILE.write_text("{}", encoding="utf-8")
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), \
             mock.patch.object(selection, "_get_json", return_value={
                 "name": "models/gemini-test", "inputTokenLimit": 100000,
                 "outputTokenLimit": 8192,
             }) as fetch:
            metadata = selection.get_selected_model_details("gemini", "gemini-test")
        self.assertIn("key=test-key", fetch.call_args.args[0])
        self.assertIsNone(fetch.call_args.args[1])
        self.assertEqual(metadata["context_window"], 100000)
        self.assertEqual(metadata["max_output_tokens"], 8192)

    def test_detail_lookup_failure_is_non_blocking_and_does_not_create_empty_entry(self):
        selection.CONFIG_FILE.write_text("{}", encoding="utf-8")
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), \
             mock.patch.object(selection, "_get_json", side_effect=RuntimeError("not found")):
            self.assertEqual(selection.get_selected_model_details("openai", "retired-model"), {})
        model_path = selection.CONFIG_FILE.parent / "model_config.json"
        self.assertFalse(model_path.exists())

    def test_kilo_metadata_maps_modality_string_and_supported_parameters(self):
        selection.MODEL_CATALOGUE_ITEMS["kilo-free"] = {
            "vendor/vision:free": {
                "id": "vendor/vision:free",
                "architecture": {"modality": "text+image->text"},
                "supported_parameters": ["tools", "response_format", "reasoning"],
                "top_provider": {"context_length": 128000,
                                 "max_completion_tokens": 12000},
            }
        }
        metadata = selection.get_selected_model_details("kilo-free", "vendor/vision:free")
        self.assertEqual(metadata["context_window"], 128000)
        self.assertEqual(metadata["max_output_tokens"], 12000)
        self.assertEqual(metadata["supported_input_types"], ["text", "image"])
        self.assertEqual(metadata["supported_output_types"], ["text"])
        self.assertTrue(metadata["supports_tools"])
        self.assertTrue(metadata["supports_structured_output"])
        self.assertTrue(metadata["supports_reasoning"])

    def test_metadata_update_preserves_existing_attributes_and_user_overrides(self):
        path = selection.CONFIG_FILE.parent / "model_config.json"
        path.write_text(json.dumps({
            "version": 1,
            "models": {"openai::model-x": {
                "context_window": 64000, "supports_tools": True,
                "vendor_metadata": "keep-me"
            }},
            "overrides": {"openai::model-x": {"context_window": 96000}},
        }), encoding="utf-8")
        selection._save_discovered_metadata("openai", "model-x", {
            "context_window": 128000, "max_output_tokens": 4096,
            "supports_tools": False,
        })
        resolved = get_model_config("openai", "model-x", load_model_config(path))
        self.assertEqual(resolved["context_window"], 96000)
        self.assertEqual(resolved["max_output_tokens"], 4096)
        self.assertFalse(resolved["supports_tools"])
        self.assertEqual(resolved["vendor_metadata"], "keep-me")

    def test_selected_model_is_saved_even_when_detail_lookup_returns_nothing(self):
        with mock.patch("builtins.input", side_effect=["4", "1"]), \
             mock.patch("builtins.print"), \
             mock.patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), \
             mock.patch.object(selection, "get_available_models", return_value=["gpt-test"]), \
             mock.patch.object(selection, "get_selected_model_details", return_value={}):
            result = selection.initialize_provider()
        self.assertEqual(result, ("openai", "gpt-test"))
        saved = json.loads(selection.CONFIG_FILE.read_text(encoding="utf-8"))
        self.assertEqual(saved["provider"], "openai")
        self.assertEqual(saved["model"], "gpt-test")

    def test_provider_config_and_model_config_are_merged_without_mutating_source(self):
        selection.CONFIG_FILE.write_text(json.dumps({"provider": "openai"}), encoding="utf-8")
        model_path = selection.CONFIG_FILE.parent / "model_config.json"
        model_path.write_text(json.dumps({"version": 1, "models": {
            "openai::gpt-test": {"context_window": 123456}
        }}), encoding="utf-8")
        merged = selection.load_config()
        self.assertEqual(merged["model_config"]["models"]["openai::gpt-test"]["context_window"], 123456)
        self.assertEqual(json.loads(selection.CONFIG_FILE.read_text()), {"provider": "openai"})


if __name__ == "__main__":
    unittest.main()
