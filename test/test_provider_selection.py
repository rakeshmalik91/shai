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
        saved = json.loads(selection.CONFIG_FILE.read_text(encoding="utf-8"))
        self.assertEqual(saved["context_window_tokens"], 131072)
        self.assertEqual(
            saved["model_context_windows"]["kilo-free::provider/model-a:free"],
            131072
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
