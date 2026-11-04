import tempfile
import unittest
from pathlib import Path

from modules.file_change_display import _bounded_diff, load_settings


class FileChangeDisplayTests(unittest.TestCase):
    def test_truncates_large_diff_and_preserves_head_tail(self):
        settings = {
            "enabled": True,
            "max_diff_lines": 10,
            "max_diff_chars": 200,
            "preserve_head_lines": 3,
            "preserve_tail_lines": 2,
        }
        diff = "".join("line-{}\n".format(i) for i in range(30))
        preview, truncated, omitted_lines, omitted_chars = _bounded_diff(diff, settings)
        self.assertTrue(truncated)
        self.assertLessEqual(len(preview), 200)
        self.assertIn("line-0", preview)
        self.assertIn("line-29", preview)
        self.assertGreater(omitted_lines, 0)
        self.assertGreater(omitted_chars, 0)

    def test_settings_are_loaded_from_config(self):
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / "shai_config.json"
            config.write_text('{"file_change_display":{"max_diff_lines":12}}')
            settings = load_settings(config)
            self.assertEqual(settings["max_diff_lines"], 12)
            self.assertEqual(settings["max_diff_chars"], 16000)


if __name__ == "__main__":
    unittest.main()
