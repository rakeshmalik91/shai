import contextlib
import io
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.reasoning_handlers import extract_reasoning_summary, display_reasoning_summary


class ReasoningHandlerTests(unittest.TestCase):
    def test_kilo_extracts_explicit_summary_block(self):
        response = {"choices": [{"message": {
            "reasoning": "PRIVATE RAW REASONING MUST NOT DISPLAY",
            "reasoning_details": [
                {"type": "reasoning.summary", "summary": "Checked the call graph and found the issue."}
            ],
            "content": "The fix is ready."
        }}]}
        self.assertEqual(
            extract_reasoning_summary("kilo", response),
            "Checked the call graph and found the issue."
        )

    def test_openrouter_does_not_expose_raw_reasoning(self):
        response = {"choices": [{"message": {
            "reasoning": "private thoughts",
            "reasoning_content": "private thoughts alias",
            "content": "Final answer"
        }}]}
        self.assertEqual(extract_reasoning_summary("openrouter", response), "")

    def test_anthropic_does_not_expose_thinking_block(self):
        response = {"content": [
            {"type": "thinking", "thinking": "private internal thought", "signature": "abc"},
            {"type": "text", "text": "Final answer"}
        ]}
        self.assertEqual(extract_reasoning_summary("anthropic", response), "")

    def test_anthropic_explicit_summary_is_extracted(self):
        response = {"content": [
            {"type": "thinking_summary", "text": "Compared two implementations."}
        ]}
        self.assertEqual(
            extract_reasoning_summary("anthropic", response),
            "Compared two implementations."
        )

    def test_gemini_summary_field_is_extracted(self):
        response = {"choices": [{"message": {
            "thought_summary": "Validated the expected output."
        }}]}
        self.assertEqual(
            extract_reasoning_summary("gemini", response),
            "Validated the expected output."
        )

    def test_display_uses_separate_thinking_summary_section(self):
        response = {"choices": [{"message": {
            "reasoning_summary": "Reviewed the failing test."
        }}]}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            displayed = display_reasoning_summary("openai", response)
        self.assertTrue(displayed)
        self.assertIn("[Thinking summary]", output.getvalue())
        self.assertIn("Reviewed the failing test.", output.getvalue())

    def test_unknown_provider_is_safe(self):
        self.assertEqual(extract_reasoning_summary("unknown", {"foo": "bar"}), "")


if __name__ == "__main__":
    unittest.main()
