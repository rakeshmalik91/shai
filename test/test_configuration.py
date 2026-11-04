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
        # Only True is treated as True, everything else is False
        self.assertEqual(settings["duplicate_call_detection"], False)
        self.assertEqual(settings["repeat_call_warning"], False)


if __name__ == "__main__":
    unittest.main()