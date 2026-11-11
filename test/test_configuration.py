import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import agent


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = pathlib.Path(self.temp_dir.name) / "shai_config.json"
        self.original_config_file = agent.CONFIG_FILE
        agent.CONFIG_FILE = self.config_path

    def tearDown(self):
        agent.CONFIG_FILE = self.original_config_file
        self.temp_dir.cleanup()

    def write_config(self, data):
        self.config_path.write_text(json.dumps(data), encoding="utf-8")

    def test_tool_safeguards_invalid_boolean_flags(self):
        """Test that invalid boolean flags are normalized."""
        self.write_config({
            "tool_safeguards": {
                "duplicate_call_detection": "yes",
                "repeat_call_warning": 0
            }
        })
        settings = agent.load_tool_safeguards()
        self.assertEqual(settings["duplicate_call_detection"], False)
        self.assertEqual(settings["repeat_call_warning"], False)

    def test_load_config_handles_missing_file_gracefully(self):
        if self.config_path.exists():
            self.config_path.unlink()
        self.assertEqual(agent.load_config(), {})

    def test_load_config_handles_malformed_json_gracefully(self):
        self.config_path.write_text("NOT_VALID_JSON{:::}", encoding="utf-8")
        self.assertEqual(agent.load_config(), {})

    def test_load_config_rejects_non_dict_root(self):
        self.config_path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        self.assertEqual(agent.load_config(), {})

    def test_load_config_merges_split_config_files(self):
        self.write_config({"provider": "kilo-free", "model": "cohere/north-mini-code:free"})
        split_file = pathlib.Path(self.temp_dir.name) / "model_config.json"
        split_file.write_text(json.dumps({"version": 1, "models": {
            "kilo-free::cohere/north-mini-code:free": {"context_window": 256000}
        }}), encoding="utf-8")
        loaded = agent.load_config()
        self.assertIn("model_config", loaded)
        self.assertEqual(loaded["model_config"]["models"]["kilo-free::cohere/north-mini-code:free"]["context_window"], 256000)

    def test_load_config_preserves_explicit_overrides_over_split_configs(self):
        self.write_config({
            "provider": "kilo-free",
            "model": "cohere/north-mini-code:free",
            "model_config": {"version": 1, "models": {"kilo-free::cohere/north-mini-code:free": {"context_window": 500000}}}
        })
        split_file = pathlib.Path(self.temp_dir.name) / "model_config.json"
        split_file.write_text(json.dumps({"version": 1, "models": {
            "kilo-free::cohere/north-mini-code:free": {"context_window": 256000},
            "other::model": {"context_window": 128000}
        }}), encoding="utf-8")
        loaded = agent.load_config()
        self.assertEqual(loaded["model_config"]["models"]["kilo-free::cohere/north-mini-code:free"]["context_window"], 500000)
        self.assertEqual(loaded["model_config"]["models"]["other::model"]["context_window"], 128000)

    def test_load_api_timeout_seconds_defaults_and_validation(self):
        # Default when missing
        self.write_config({})
        self.assertEqual(agent.load_api_timeout_seconds(), agent.DEFAULT_API_TIMEOUT_SECONDS)

        # Valid custom timeout
        self.write_config({"api_timeout_seconds": 120})
        self.assertEqual(agent.load_api_timeout_seconds(), 120)

        # Invalid types: boolean, negative integer, zero, string
        for invalid in (True, False, -10, 0, "60", 3.14):
            with self.subTest(invalid=invalid):
                self.write_config({"api_timeout_seconds": invalid})
                self.assertEqual(agent.load_api_timeout_seconds(), agent.DEFAULT_API_TIMEOUT_SECONDS)

    def test_load_max_tool_call_iterations_clamping_and_validation(self):
        # Value exceeding hard cap clamps to hard max
        self.write_config({"max_tool_call_iterations": 2000})
        self.assertEqual(agent.load_max_tool_call_iterations(), agent.HARD_MAX_TOOL_CALL_ITERATIONS)

        # Invalid types: boolean, negative, string
        for invalid in (True, False, -5, 0, "100"):
            with self.subTest(invalid=invalid):
                self.write_config({"max_tool_call_iterations": invalid})
                with mock.patch("builtins.print"):
                    self.assertEqual(agent.load_max_tool_call_iterations(), agent.DEFAULT_MAX_TOOL_CALL_ITERATIONS)

    def test_load_tool_call_display_settings_validation(self):
        self.write_config({
            "tool_call_display": {
                "show_parameters": "yes",
                "max_argument_chars": -50
            }
        })
        with mock.patch("builtins.print"):
            settings = agent.load_tool_call_display_settings()
        self.assertFalse(settings["show_parameters"])
        self.assertEqual(settings["max_argument_chars"], agent.DEFAULT_TOOL_CALL_DISPLAY["max_argument_chars"])

        self.write_config({
            "tool_call_display": {
                "show_parameters": True,
                "max_argument_chars": 500
            }
        })
        settings = agent.load_tool_call_display_settings()
        self.assertTrue(settings["show_parameters"])
        self.assertEqual(settings["max_argument_chars"], 500)

    def test_load_retry_settings_exhaustive_validation(self):
        self.write_config({
            "retry": {
                "max_retries": True,  # boolean rejected as int
                "initial_backoff_seconds": "invalid",
                "backoff_multiplier": 0.5,  # clamped to minimum 1.0
                "jitter_fraction": 1.5,     # clamped to maximum 1.0
                "retry_http_statuses": [400, 429, 500, "502", True, 999],  # 400 excluded, strings/booleans/out-of-range excluded
            }
        })
        settings = agent.load_retry_settings()
        self.assertEqual(settings["max_retries"], agent.DEFAULT_RETRY_SETTINGS["max_retries"])
        self.assertEqual(settings["backoff_multiplier"], 1.0)
        self.assertEqual(settings["jitter_fraction"], 1.0)
        self.assertNotIn(400, settings["retry_http_statuses"])
        self.assertEqual(settings["retry_http_statuses"], [429, 500])

    def test_load_test_execution_settings_clamps_combined_streams(self):
        self.write_config({
            "test_execution": {
                "max_output_bytes": 1000,
                "max_stdout_bytes": 800,
                "max_stderr_bytes": 600,
            }
        })
        settings = agent.load_test_execution_settings()
        self.assertEqual(settings["max_output_bytes"], 1000)
        # Combined stdout + stderr must not exceed total max_output_bytes
        self.assertLessEqual(settings["max_stdout_bytes"] + settings["max_stderr_bytes"], 1000)

    def test_approval_granted_hierarchy(self):
        # 1. Tool override takes precedence over category and all
        self.write_config({
            "approve": {
                "all": True,
                "file_edit": True,
                "tools": {"patch_file": False, "read_file": True}
            }
        })
        self.assertFalse(agent.approval_granted(category="file_edit", tool="patch_file"))
        self.assertTrue(agent.approval_granted(category="file_edit", tool="read_file"))

        # 2. Category override takes precedence over global all
        self.write_config({
            "approve": {
                "all": True,
                "execution": False,
            }
        })
        self.assertFalse(agent.approval_granted(category="execution", tool="run_checks"))

        # 3. Global all applies when category/tool not explicitly set
        self.write_config({
            "approve": {
                "all": True,
            }
        })
        self.assertTrue(agent.approval_granted(category="file_edit", tool="patch_file"))

        # 4. Defaults to False when nothing approved
        self.write_config({"approve": {}})
        self.assertFalse(agent.approval_granted(category="file_edit", tool="patch_file"))

    def test_positive_int_setting_helper(self):
        config = {
            "valid": 42,
            "bool_val": True,
            "negative": -5,
            "zero": 0,
            "str_val": "100",
            "float_val": 42.5
        }
        with mock.patch("builtins.print"):
            self.assertEqual(agent.positive_int_setting(config, "valid", 10), 42)
            self.assertEqual(agent.positive_int_setting(config, "bool_val", 10), 10)
            self.assertEqual(agent.positive_int_setting(config, "negative", 10), 10)
            self.assertEqual(agent.positive_int_setting(config, "zero", 10), 10)
            self.assertEqual(agent.positive_int_setting(config, "str_val", 10), 10)
            self.assertEqual(agent.positive_int_setting(config, "float_val", 10), 10)
            self.assertEqual(agent.positive_int_setting(config, "missing", 10), 10)

    def test_load_agent_safeguards_bounds(self):
        self.write_config({
            "agent_safeguards": {
                "max_read_file_lines": 1000,  # capped at 500
                "max_invalid_argument_attempts_per_tool": 10,  # capped at 5
                "default_read_file_lines": 600,  # clamped to max_read_file_lines
            }
        })
        settings = agent.load_agent_safeguards()
        self.assertLessEqual(settings["max_read_file_lines"], 500)
        self.assertLessEqual(settings["max_invalid_argument_attempts_per_tool"], 5)
        self.assertLessEqual(settings["default_read_file_lines"], settings["max_read_file_lines"])

    def test_save_approval_settings_does_not_leak_split_configs_into_shai_config(self):
        self.write_config({"provider": "kilo-free", "model": "test-model"})
        # Place sibling split config files in the same directory
        (pathlib.Path(self.temp_dir.name) / "model_config.json").write_text(
            json.dumps({"version": 1, "models": {"kilo-free::test-model": {"context_window": 128000}}}),
            encoding="utf-8"
        )
        (pathlib.Path(self.temp_dir.name) / "cli_config.json").write_text(
            json.dumps({"tool_call_display": {"show_parameters": True}}),
            encoding="utf-8"
        )
        agent.save_approval_settings({"all": True, "tools": {"run_checks": True}})
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["provider"], "kilo-free")
        self.assertEqual(saved["model"], "test-model")
        self.assertTrue(saved["approve"]["all"])
        # Crucial check: split files must not be copied/leaked into shai_config.json
        self.assertNotIn("model_config", saved)
        self.assertNotIn("tool_call_display", saved)

    def test_provider_selection_load_config_preserves_shai_config_overrides(self):
        from modules import provider_selection
        orig_config_file = provider_selection.CONFIG_FILE
        provider_selection.CONFIG_FILE = self.config_path
        try:
            self.write_config({
                "providers": {
                    "kilo-free": {"timeout_seconds": 99}
                }
            })
            (pathlib.Path(self.temp_dir.name) / "provider_config.json").write_text(
                json.dumps({
                    "providers": {
                        "kilo-free": {"timeout_seconds": 300, "auth_type": "bearer"}
                    }
                }),
                encoding="utf-8"
            )
            loaded = provider_selection.load_config()
            self.assertIn("providers", loaded)
            self.assertEqual(loaded["providers"]["kilo-free"]["timeout_seconds"], 99)
            self.assertEqual(loaded["providers"]["kilo-free"]["auth_type"], "bearer")
        finally:
            provider_selection.CONFIG_FILE = orig_config_file


if __name__ == "__main__":
    unittest.main()