import pathlib
import sys
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.context_meter import (
    estimate_text_tokens, estimate_messages_tokens, get_context_window,
    extract_usage, format_context_status
)


class ContextMeterTests(unittest.TestCase):
    def test_estimates_nonempty_prompt(self):
        self.assertGreater(estimate_text_tokens("hello world"), 0)
        self.assertGreater(
            estimate_messages_tokens([{"role": "user", "content": "hello"}]), 0
        )

    def test_reads_model_specific_context_limit(self):
        config = {
            "model_context_windows": {
                "kilo-free::example:model": 65536
            }
        }
        self.assertEqual(
            get_context_window("kilo-free", "example:model", config), 65536
        )

    def test_does_not_guess_unknown_context_limit(self):
        self.assertIsNone(get_context_window("unknown", "model", {}))

    def test_extracts_openai_usage(self):
        self.assertEqual(
            extract_usage({"usage": {
                "prompt_tokens": 120, "completion_tokens": 30,
                "total_tokens": 150
            }}),
            {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}
        )

    def test_extracts_anthropic_usage(self):
        self.assertEqual(
            extract_usage({"usage": {
                "input_tokens": 100, "output_tokens": 25
            }}, "anthropic"),
            {"prompt_tokens": 100, "completion_tokens": 25, "total_tokens": 125}
        )

    def test_formats_percentage_when_limit_is_known(self):
        self.assertIn("25.0%", format_context_status(250, 1000))

    def test_discloses_unknown_limit_instead_of_fabricating_percentage(self):
        self.assertIn(
            "context limit unknown", format_context_status(250, None)
        )


if __name__ == "__main__":
    unittest.main()
