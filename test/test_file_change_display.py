import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from modules.file_change_display import _bounded_diff, load_settings, print_change_preview


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

    def test_small_diff_is_not_truncated(self):
        settings = {
            "enabled": True,
            "max_diff_lines": 10,
            "max_diff_chars": 200,
            "preserve_head_lines": 3,
            "preserve_tail_lines": 2,
        }
        diff = "line-1\nline-2\nline-3\n"
        preview, truncated, omitted_lines, omitted_chars = _bounded_diff(diff, settings)
        self.assertFalse(truncated)
        self.assertEqual(preview, diff)
        self.assertEqual(omitted_lines, 0)
        self.assertEqual(omitted_chars, 0)

    def test_empty_diff_returns_cleanly(self):
        settings = {
            "enabled": True,
            "max_diff_lines": 10,
            "max_diff_chars": 200,
            "preserve_head_lines": 3,
            "preserve_tail_lines": 2,
        }
        preview, truncated, omitted_lines, omitted_chars = _bounded_diff("", settings)
        self.assertFalse(truncated)
        self.assertEqual(preview, "")
        self.assertEqual(omitted_lines, 0)
        self.assertEqual(omitted_chars, 0)

    def test_settings_are_loaded_from_config(self):
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / "shai_config.json"
            config.write_text('{"file_change_display":{"max_diff_lines":12}}')
            settings = load_settings(config)
            self.assertEqual(settings["max_diff_lines"], 12)
            self.assertEqual(settings["max_diff_chars"], 16000)

    def test_settings_fallback_on_corrupt_or_invalid_config(self):
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / "shai_config.json"
            config.write_text('INVALID_JSON')
            settings = load_settings(config)
            self.assertEqual(settings["max_diff_lines"], 160)
            self.assertTrue(settings["enabled"])

    def test_settings_normalization_of_invalid_types(self):
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / "shai_config.json"
            config.write_text('{"file_change_display":{"max_diff_lines":-10, "max_diff_chars":true, "enabled":"yes"}}')
            settings = load_settings(config)
            self.assertEqual(settings["max_diff_lines"], 1)
            self.assertEqual(settings["max_diff_chars"], 1)
            self.assertFalse(settings["enabled"])

    def test_print_change_preview_enabled_and_disabled(self):
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / "shai_config.json"
            config.write_text('{"file_change_display":{"enabled":false}}')
            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                print_change_preview("+ line\n", "Diff", "Modified sample.py", config_path=config)
            output = captured.getvalue()
            self.assertIn("Diff preview disabled", output)

            config.write_text('{"file_change_display":{"enabled":true}}')
            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                print_change_preview("+ line\n", "Diff", "Modified sample.py", config_path=config)
            output = captured.getvalue()
            self.assertIn("+ line", output)


if __name__ == "__main__":
    unittest.main()
