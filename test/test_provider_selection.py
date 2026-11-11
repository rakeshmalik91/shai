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
        selection._state_dir().mkdir(parents=True, exist_ok=True)

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

    def test_model_catalogue_fetches_ids_without_saving_metadata(self):
        with mock.patch.dict("os.environ", {"KILO_API_KEY": "test-key"}):
            with mock.patch.object(selection, "_get_json", return_value={
                "data": [{
                    "id": "provider/model-a:free",
                    "context_length": 131072,
                    "max_completion_tokens": 8192,
                    "tool_calling": True,
                }]
            }):
                models = selection.get_available_models("kilo-free")
        self.assertEqual(models, ["provider/model-a:free"])
        model_config_path = selection._state_dir() / "model_config.json"
        self.assertFalse(model_config_path.exists())

    def test_saved_model_context_override_is_preserved_and_preferred(self):
        selection.CONFIG_FILE.write_text("{}", encoding="utf-8")
        context_path = selection._state_dir() / "model_config.json"
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

    def test_auto_select_saved_model_skips_live_model_discovery(self):
        selection.CONFIG_FILE.write_text(json.dumps({
            "provider": "kilo-free",
            "model": "provider/model-a:free",
        }), encoding="utf-8")
        with mock.patch.object(selection, "get_available_models",
                               side_effect=AssertionError("startup must not fetch model catalogue")) as discover, \
             mock.patch("builtins.print"):
            result = selection.initialize_provider(auto_select=True)
        self.assertEqual(result, ("kilo-free", "provider/model-a:free"))
        discover.assert_not_called()

    def test_provider_zero_reuses_saved_provider_and_model(self):
        selection.CONFIG_FILE.write_text(json.dumps({
            "provider": "kilo-free",
            "model": "provider/model-a:free",
        }), encoding="utf-8")
        with mock.patch("builtins.input", return_value="0"), mock.patch("builtins.print"):
            result = selection.initialize_provider()
        self.assertEqual(result, ("kilo-free", "provider/model-a:free"))

    def test_interactive_selection_persists_only_selected_kilo_model_metadata(self):
        selection.CONFIG_FILE.write_text(json.dumps({
            "provider": "kilo-free",
            "model": "old/model:free",
        }), encoding="utf-8")
        catalogue_payload = {"data": [
            {"id": "vendor/first-model:free", "context_length": 32768},
            {"id": "vendor/new-model:free", "context_length": 65536,
             "top_provider": {"max_completion_tokens": 4096},
             "supported_parameters": ["tools", "tool_choice", "response_format"],
             "architecture": {"input_modalities": ["text", "image"],
                              "output_modalities": ["text"]}},
        ]}
        with mock.patch("builtins.input", side_effect=["6", "2"]), \
             mock.patch("builtins.print"), \
             mock.patch.dict("os.environ", {"KILO_API_KEY": "test-key"}), \
             mock.patch.object(selection, "_get_json", return_value=catalogue_payload) as get_json:
            result = selection.initialize_provider()

        self.assertEqual(result, ("kilo-free", "vendor/new-model:free"))
        self.assertEqual(get_json.call_count, 1, "Kilo must not make an undocumented per-model request")
        saved = json.loads((selection._state_dir() / "model_config.json").read_text())
        self.assertEqual(set(saved["models"]), {"kilo-free::vendor/new-model:free"})
        metadata = saved["models"]["kilo-free::vendor/new-model:free"]
        self.assertEqual(metadata["context_window"], 65536)
        self.assertEqual(metadata["max_output_tokens"], 4096)
        self.assertTrue(metadata["supports_tools"])
        self.assertTrue(metadata["supports_structured_output"])
        self.assertEqual(metadata["supported_input_types"], ["text", "image"])


    def test_selection_is_saved_before_model_detail_lookup(self):
        with mock.patch("builtins.input", side_effect=["6", "1"]), \
             mock.patch("builtins.print"), \
             mock.patch.dict("os.environ", {"KILO_API_KEY": "test-key"}), \
             mock.patch.object(selection, "get_available_models", return_value=[
                 "vendor/selected-model:free"
             ]), \
             mock.patch.object(selection, "get_selected_model_details",
                               side_effect=lambda provider, model: (
                                   self.assertEqual(
                                       json.loads((selection._state_dir() / "model_routing.json").read_text())["default"]["provider"],
                                       provider
                                   ),
                                   self.assertEqual(
                                       json.loads((selection._state_dir() / "model_routing.json").read_text())["default"]["model"],
                                       model
                                   ),
                                   {}
                               )[-1]):
            result = selection.initialize_provider()

        self.assertEqual(result, ("kilo-free", "vendor/selected-model:free"))
        saved = json.loads((selection._state_dir() / "model_routing.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["default"]["provider"], "kilo-free")
        self.assertEqual(saved["default"]["model"], "vendor/selected-model:free")
        self.assertFalse((selection._state_dir() / "provider_model.json").exists())

    def test_kilo_missing_selected_catalogue_record_is_non_blocking(self):
        selection.MODEL_CATALOGUE_ITEMS["kilo-free"] = {}
        with mock.patch.dict("os.environ", {"KILO_API_KEY": "test-key"}), \
             mock.patch.object(selection, "_get_json",
                               side_effect=AssertionError("no undocumented detail endpoint")) as get_json:
            metadata = selection.get_selected_model_details(
                "kilo-free", "vendor/discontinued:free"
            )
        self.assertEqual(metadata, {})
        get_json.assert_not_called()

    def test_save_provider_preserves_other_configuration(self):
        selection.CONFIG_FILE.write_text(json.dumps({
            "yes_to_all": True,
            "max_tool_call_iterations": 100,
            "retry": {"max_retries": 4},
        }), encoding="utf-8")
        selection.save_config("kilo-free", "provider/model-a:free")
        saved = json.loads(selection.CONFIG_FILE.read_text(encoding="utf-8"))
        selection_state = json.loads(
            (selection._state_dir() / "model_routing.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("provider", saved)
        self.assertNotIn("model", saved)
        self.assertTrue(saved["yes_to_all"])
        self.assertEqual(saved["max_tool_call_iterations"], 100)
        self.assertEqual(saved["retry"]["max_retries"], 4)
        self.assertEqual(selection_state["default"]["provider"], "kilo-free")
        self.assertEqual(selection_state["default"]["model"], "provider/model-a:free")
        self.assertFalse((selection._state_dir() / "provider_model.json").exists())


if __name__ == "__main__":
    unittest.main()
