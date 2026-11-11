import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules import provider_selection as selection


class ProviderSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_config_file = selection.CONFIG_FILE
        selection.CONFIG_FILE = pathlib.Path(self.temp_dir.name) / "shai_config.json"

    def tearDown(self):
        selection.CONFIG_FILE = self.original_config_file
        self.temp_dir.cleanup()

    def test_kilo_free_only_returns_free_models(self):
        with mock.patch.dict("os.environ", {"KILO_API_KEY": "test-key"}):
            with mock.patch.object(selection, "_get_json", return_value={
                "data": [
                    {"id": "provider/model-a:free"},
                    {"id": "provider/model-b"},
                    {"id": "provider/model-c:free"},
                ]
            }):
                models = selection.get_available_models("kilo-free")
        self.assertEqual(models, ["provider/model-a:free", "provider/model-c:free"])

    def test_kilo_hides_free_models_but_kilo_free_keeps_them(self):
        payload = {"data": [
            {"id": "vendor/paid-model"},
            {"id": "vendor/free-model:free"},
        ]}
        with mock.patch.dict("os.environ", {"KILO_API_KEY": "test-key"}):
            with mock.patch.object(selection, "_get_json", return_value=payload):
                regular = selection.get_available_models("kilo")
            with mock.patch.object(selection, "_get_json", return_value=payload):
                free = selection.get_available_models("kilo-free")
        self.assertEqual(regular, ["vendor/paid-model"])
        self.assertEqual(free, ["vendor/free-model:free"])

    def test_ollama_is_keyless_and_reads_local_tags(self):
        with mock.patch.object(selection, "_get_json", return_value={
            "models": [{"name": "llama3.2:latest"}, {"name": "qwen2.5:7b"}]
        }) as get_json:
            models = selection.get_available_models("ollama")
        self.assertEqual(models, ["llama3.2:latest", "qwen2.5:7b"])
        self.assertEqual(get_json.call_args.args[0],
                         "http://127.0.0.1:11434/api/tags")
        self.assertIsNone(get_json.call_args.args[1])

    def test_provider_endpoints_and_auth_are_configurable(self):
        selection.CONFIG_FILE.write_text(json.dumps({
            "providers": {
                "ollama": {
                    "base_url": "http://192.168.1.20:11434/v1",
                    "models_url": "http://192.168.1.20:11434/api/tags",
                    "auth_type": "none",
                    "key_env": None,
                    "headers": {"X-Test": "configured"},
                }
            }
        }), encoding="utf-8")
        config = selection.get_provider_config("ollama")
        self.assertEqual(config["url"],
                         "http://192.168.1.20:11434/v1/chat/completions")
        self.assertEqual(selection.build_provider_url("ollama", "models"),
                         "http://192.168.1.20:11434/api/tags")
        headers = selection.build_provider_headers("ollama", None)
        self.assertEqual(headers["X-Test"], "configured")
        self.assertNotIn("Authorization", headers)

    def test_query_auth_url_is_configurable(self):
        selection.CONFIG_FILE.write_text(json.dumps({
            "providers": {"gemini": {"auth_query_param": "token"}}
        }), encoding="utf-8")
        self.assertEqual(
            selection.build_provider_url("gemini", "chat", "abc"),
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions?token=abc"
        )

    def test_model_context_window_metadata_is_captured_and_saved(self):
        with mock.patch.dict("os.environ", {"KILO_API_KEY": "test-key"}):
            with mock.patch.object(selection, "_get_json", return_value={
                "data": [{
                    "id": "provider/model-a:free",
                    "context_length": 131072,
                }]
            }):
                models = selection.get_available_models("kilo-free")
        self.assertEqual(models, ["provider/model-a:free"])
        self.assertEqual(
            selection.MODEL_CONTEXT_WINDOWS["provider/model-a:free"], 131072
        )
        selection.save_config(
            "kilo-free", "provider/model-a:free",
            selection.MODEL_CONTEXT_WINDOWS["provider/model-a:free"]
        )
        saved = json.loads((selection.CONFIG_FILE.parent / "model_config.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["models"]["kilo-free::provider/model-a:free"]["context_window"], 131072)

    def test_discovery_saves_output_limit_and_supported_modalities(self):
        with mock.patch.dict("os.environ", {"KILO_API_KEY": "test-key"}):
            with mock.patch.object(selection, "_get_json", return_value={
                "data": [{
                    "id": "vendor/multimodal-model:free",
                    "context_length": 131072,
                    "max_completion_tokens": 8192,
                    "architecture": {
                        "input_modalities": ["text", "image", "audio"],
                        "output_modalities": ["text"],
                    },
                    "tool_calling": True,
                }]
            }):
                models = selection.get_available_models("kilo-free")
        self.assertEqual(models, ["vendor/multimodal-model:free"])
        saved = json.loads((selection.CONFIG_FILE.parent / "model_config.json").read_text())
        metadata = saved["models"]["kilo-free::vendor/multimodal-model:free"]
        self.assertEqual(metadata["context_window"], 131072)
        self.assertEqual(metadata["max_output_tokens"], 8192)
        self.assertEqual(metadata["supported_input_types"], ["text", "image", "audio"])
        self.assertEqual(metadata["supported_output_types"], ["text"])
        self.assertTrue(metadata["supports_tools"])

    def test_saved_model_context_override_is_preserved_and_preferred(self):
        selection.CONFIG_FILE.write_text("{}", encoding="utf-8")
        context_path = selection.CONFIG_FILE.parent / "model_config.json"
        context_path.write_text(json.dumps({"version": 1,
            "models": {"kilo-free::vendor/model": {"context_window": 65536}},
            "overrides": {"kilo-free::vendor/model": {"context_window": 98304}},
        }), encoding="utf-8")
        self.assertEqual(selection.get_saved_context_window(
            "kilo-free", "vendor/model"
        ), 98304)
        selection.save_config("kilo-free", "vendor/model", 65536)
        saved = json.loads(context_path.read_text(encoding="utf-8"))
        self.assertEqual(
            saved["overrides"]["kilo-free::vendor/model"]["context_window"],
            98304,
        )

    def test_provider_zero_reuses_saved_provider_and_model(self):
        selection.CONFIG_FILE.write_text(json.dumps({
            "provider": "kilo-free",
            "model": "provider/model-a:free",
        }), encoding="utf-8")
        with mock.patch("builtins.input", return_value="0"), mock.patch("builtins.print"):
            result = selection.initialize_provider()
        self.assertEqual(result, ("kilo-free", "provider/model-a:free"))

    def test_save_provider_preserves_other_configuration(self):
        selection.CONFIG_FILE.write_text(json.dumps({
            "yes_to_all": True,
            "max_tool_call_iterations": 100,
            "retry": {"max_retries": 4},
        }), encoding="utf-8")
        selection.save_config("kilo-free", "provider/model-a:free")
        saved = json.loads(selection.CONFIG_FILE.read_text(encoding="utf-8"))
        self.assertEqual(saved["provider"], "kilo-free")
        self.assertEqual(saved["model"], "provider/model-a:free")
        self.assertTrue(saved["yes_to_all"])
        self.assertEqual(saved["max_tool_call_iterations"], 100)
        self.assertEqual(saved["retry"]["max_retries"], 4)


if __name__ == "__main__":
    unittest.main()
