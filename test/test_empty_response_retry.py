import logging
import unittest

from modules.empty_response_retry import (
    request_with_empty_response_retries, response_has_output,
)


class EmptyResponseRetryTests(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("test.empty_response_retry")

    def test_openai_compatible_empty_then_text_retries(self):
        calls = []
        responses = [
            {"choices": [{"message": {"content": None, "tool_calls": []}}]},
            {"choices": [{"message": {"content": "recovered", "tool_calls": []}}]},
        ]
        def request(history):
            calls.append(history)
            return responses.pop(0)
        history = [{"role": "user", "content": "original"}]
        result = request_with_empty_response_retries(
            request, history, "openai", {
                "empty_response_retry_enabled": True,
                "empty_response_max_retries": 2,
            }, self.logger, retry_delay_seconds=0,
        )
        self.assertEqual(result["choices"][0]["message"]["content"], "recovered")
        self.assertEqual(len(calls), 2)
        self.assertEqual(history, [{"role": "user", "content": "original"}])
        self.assertEqual(len(calls[0]), 1)
        self.assertEqual(len(calls[1]), 2)

    def test_anthropic_empty_then_tool_use_retries(self):
        calls = []
        responses = [
            {"content": []},
            {"content": [{"type": "tool_use", "id": "x", "name": "read_file", "input": {}}]},
        ]
        result = request_with_empty_response_retries(
            lambda history: (calls.append(history) or responses.pop(0)),
            [{"role": "user", "content": "inspect"}],
            "anthropic",
            {"empty_response_retry_enabled": True, "empty_response_max_retries": 1},
            self.logger, retry_delay_seconds=0,
        )
        self.assertEqual(result["content"][0]["type"], "tool_use")
        self.assertEqual(len(calls), 2)

    def test_retry_can_be_disabled(self):
        calls = []
        empty = {"choices": [{"message": {"content": "", "tool_calls": []}}]}
        result = request_with_empty_response_retries(
            lambda history: (calls.append(history) or empty),
            [{"role": "user", "content": "x"}], "openai",
            {"empty_response_retry_enabled": False, "empty_response_max_retries": 5},
            self.logger, retry_delay_seconds=0,
        )
        self.assertIs(result, empty)
        self.assertEqual(len(calls), 1)

    def test_max_retries_is_respected(self):
        calls = []
        empty = {"choices": [{"message": {"content": None, "tool_calls": []}}]}
        request_with_empty_response_retries(
            lambda history: (calls.append(history) or empty),
            [{"role": "user", "content": "x"}], "openai",
            {"empty_response_retry_enabled": True, "empty_response_max_retries": 2},
            self.logger, retry_delay_seconds=0,
        )
        self.assertEqual(len(calls), 3)

    def test_all_registered_provider_formats_are_supported(self):
        import json
        from pathlib import Path
        config = json.loads(
            (Path(__file__).resolve().parents[1] / "assets" / "provider_config.json").read_text()
        )
        providers = config["providers"]
        self.assertTrue(providers)
        for provider, details in providers.items():
            fmt = details.get("format")
            self.assertIn(fmt, ("openai", "anthropic"), provider)
            if fmt == "anthropic":
                self.assertTrue(response_has_output(
                    {"content": [{"type": "text", "text": "ok"}]}, fmt
                ))
            else:
                self.assertTrue(response_has_output(
                    {"choices": [{"message": {"content": "ok", "tool_calls": []}}]}, fmt
                ))

    def test_nonempty_text_and_tool_calls_are_not_retried(self):
        for response, fmt in (
            ({"choices": [{"message": {"content": "ok", "tool_calls": []}}]}, "openai"),
            ({"choices": [{"message": {"content": None, "tool_calls": [{"id": "1"}]}}]}, "openai"),
            ({"content": [{"type": "text", "text": "ok"}]}, "anthropic"),
            ({"content": [{"type": "tool_use", "id": "1"}]}, "anthropic"),
        ):
            self.assertTrue(response_has_output(response, fmt))


if __name__ == "__main__":
    unittest.main()
