import pathlib
import sys
import unittest
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import chat


class ChatTests(unittest.TestCase):
    def test_provider_switch_keeps_text_and_removes_provider_specific_content(self):
        history = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "hi"},
                {"type": "tool_use", "id": "x"},
            ]},
        ]
        cleaned = chat.history_for_new_provider(history)
        self.assertEqual(cleaned, [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ])

    def test_model_command_switches_provider_without_api_call(self):
        choices = iter([("kilo-free", "free-model:free"), ("openai", "gpt-test")])
        user_inputs = iter(["/model", "/status", "/exit"])
        with mock.patch.object(chat, "initialize_provider", side_effect=lambda: next(choices)), \
             mock.patch("builtins.input", side_effect=lambda _prompt="": next(user_inputs)), \
             mock.patch("builtins.print") as printed:
            chat.main()
        rendered = " ".join(str(call) for call in printed.call_args_list)
        self.assertIn("gpt-test", rendered)

    def test_anthropic_response_extracts_text(self):
        text, tools = chat.extract_anthropic_response({"content": [
            {"type": "text", "text": "answer"},
            {"type": "tool_use", "name": "unused", "input": {}},
        ]})
        self.assertEqual(text, "answer")
        self.assertEqual(len(tools), 1)


if __name__ == "__main__":
    unittest.main()
