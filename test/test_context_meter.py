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

    def test_estimate_text_tokens_empty_and_unicode(self):
        self.assertEqual(estimate_text_tokens(""), 0)
        self.assertEqual(estimate_text_tokens(None), 0)
        # Unicode multi-byte strings take more tokens
        ascii_est = estimate_text_tokens("hello")
        unicode_est = estimate_text_tokens("你好世界")
        self.assertGreaterEqual(unicode_est, ascii_est)

    def test_estimate_messages_tokens_with_tools_and_system(self):
        base = estimate_messages_tokens([{"role": "user", "content": "test"}])
        with_tools = estimate_messages_tokens(
            [{"role": "user", "content": "test"}],
            tools=[{"type": "function", "function": {"name": "sample_tool"}}]
        )
        with_system = estimate_messages_tokens(
            [{"role": "user", "content": "test"}],
            system_text="You are a helpful coding assistant."
        )
        self.assertGreater(with_tools, base)
        self.assertGreater(with_system, base)

    def test_reads_model_specific_context_limit(self):
        config = {
            "model_context_windows": {
                "kilo-free::example:model": 65536
            }
        }
        self.assertEqual(
            get_context_window("kilo-free", "example:model", config), 65536
        )

    def test_model_specific_override_takes_precedence_over_provider_metadata(self):
        config = {
            "model_context_windows": {"kilo-free::example:model": 65536},
            "model_context_window_overrides": {"kilo-free::example:model": 98304},
        }
        self.assertEqual(
            get_context_window("kilo-free", "example:model", config), 98304
        )

    def test_get_context_window_rejects_booleans_and_negatives(self):
        config = {
            "model_context_windows": {
                "p::bool": True,
                "p::negative": -1000,
                "p::zero": 0,
            }
        }
        self.assertIsNone(get_context_window("p", "bool", config))
        self.assertIsNone(get_context_window("p", "negative", config))
        self.assertIsNone(get_context_window("p", "zero", config))

    def test_does_not_guess_unknown_context_limit(self):
        self.assertIsNone(get_context_window("unknown", "model", {}))
        self.assertIsNone(get_context_window("unknown", "model", None))

    def test_extract_usage_openai(self):
        self.assertEqual(
            extract_usage({"usage": {
                "prompt_tokens": 120, "completion_tokens": 30,
                "total_tokens": 150
            }}),
            {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}
        )

    def test_extract_usage_anthropic(self):
        self.assertEqual(
            extract_usage({"usage": {
                "input_tokens": 100, "output_tokens": 25
            }}, "anthropic"),
            {"prompt_tokens": 100, "completion_tokens": 25, "total_tokens": 125}
        )

    def test_extract_usage_calculates_total_when_missing(self):
        usage = extract_usage({"usage": {
            "prompt_tokens": 80, "completion_tokens": 20
        }})
        self.assertEqual(usage.get("total_tokens"), 100)

    def test_extract_usage_malformed_returns_empty(self):
        self.assertEqual(extract_usage(None), {})
        self.assertEqual(extract_usage("not a dict"), {})
        self.assertEqual(extract_usage({}), {})
        self.assertEqual(extract_usage({"usage": "not a dict"}), {})

    def test_formats_percentage_when_limit_is_known(self):
        status = format_context_status(250, 1000)
        self.assertIn("25.0%", status)
        self.assertIn("250", status)
        self.assertIn("1,000", status)

    def test_formats_exact_and_generated_tokens(self):
        exact_status = format_context_status(250, 1000, exact=True)
        self.assertIn("Prompt:", exact_status)

        used_status = format_context_status(250, 1000, generated_tokens=50)
        self.assertIn("Context used: 300 / 1,000", used_status)

    def test_discloses_unknown_limit_instead_of_fabricating_percentage(self):
        self.assertIn(
            "context limit unknown", format_context_status(250, None)
        )

    def test_fallback_to_assets_context_window_config_for_known_models(self):
        # Even with empty config, default model context limits from assets are resolved
        self.assertEqual(
            get_context_window("kilo-free", "cohere/north-mini-code:free", {}), 256000
        )
        self.assertEqual(
            get_context_window("gemini", "gemini-3.5-flash", None), 1048576
        )

    def test_thinking_context_status_known_for_default_model(self):
        limit = get_context_window("kilo-free", "cohere/north-mini-code:free", {})
        status = format_context_status(1200, limit)
        self.assertNotIn("limit unknown", status)
        self.assertIn("256,000", status)
        self.assertIn("0.5%", status)


if __name__ == "__main__":
    unittest.main()
