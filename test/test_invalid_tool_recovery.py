import json
import pathlib
import sys
import unittest
import urllib.error
from io import BytesIO
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import agent


class InvalidToolCallDetectionTests(unittest.TestCase):
    def test_detects_nested_provider_error(self):
        detail = (
            '{"error":{"message":"Provider returned error",'
            '"metadata":{"raw":"{\\"message\\":'
            '\\"invalid tool call provided\\"}"}}}'
        )
        self.assertTrue(agent.is_invalid_tool_call_error(detail))

    def test_does_not_misclassify_rate_limit(self):
        self.assertFalse(agent.is_invalid_tool_call_error("rate limit exceeded"))

    def test_http_400_invalid_tool_error_is_classified(self):
        original_provider = getattr(agent, "PROVIDER", None)
        original_model = getattr(agent, "MODEL", None)
        original_key = __import__("os").environ.get("KILO_API_KEY")
        agent.PROVIDER = "kilo-free"
        agent.MODEL = "test-model"
        __import__("os").environ["KILO_API_KEY"] = "test-key"
        body = b'{"error":{"message":"Provider returned error",' \
               b'"metadata":{"raw":"invalid tool call provided"}}}'
        http_error = urllib.error.HTTPError(
            "https://example.invalid", 400, "Bad Request", {}, BytesIO(body)
        )
        try:
            with mock.patch.object(agent.urllib.request, "urlopen", side_effect=http_error):
                with self.assertRaises(agent.APIRequestError) as caught:
                    agent.api_request([])
            self.assertEqual(caught.exception.status_code, 400)
            self.assertTrue(caught.exception.invalid_tool_call)
        finally:
            if original_provider is None:
                delattr(agent, "PROVIDER")
            else:
                agent.PROVIDER = original_provider
            if original_model is None:
                delattr(agent, "MODEL")
            else:
                agent.MODEL = original_model
            if original_key is None:
                __import__("os").environ.pop("KILO_API_KEY", None)
            else:
                __import__("os").environ["KILO_API_KEY"] = original_key


class RetryPolicyTests(unittest.TestCase):
    def test_http_400_is_removed_from_configured_retry_statuses(self):
        original = agent.CONFIG_FILE
        path = PROJECT_ROOT / "test" / "temporary_retry_config.json"
        agent.CONFIG_FILE = path
        try:
            path.write_text(json.dumps({"retry": {"retry_http_statuses": [400, 429, 503]}}))
            settings = agent.load_retry_settings()
            self.assertEqual(settings["retry_http_statuses"], [429, 503])
        finally:
            agent.CONFIG_FILE = original
            if path.exists():
                path.unlink()

    def test_http_400_is_not_retried_even_if_runtime_settings_include_it(self):
        original = agent.RETRY_SETTINGS
        agent.RETRY_SETTINGS = dict(original)
        agent.RETRY_SETTINGS.update({
            "enabled": True,
            "max_retries": 10,
            "retry_http_statuses": [400],
        })
        error = agent.APIRequestError(
            "invalid tool call provided", status_code=400,
            invalid_tool_call=True
        )
        try:
            with mock.patch.object(agent, "api_request", side_effect=error) as request, \
                 mock.patch.object(agent.time, "sleep") as sleep:
                with self.assertRaises(agent.APIRequestError):
                    agent.api_request_with_retries([])
            request.assert_called_once()
            sleep.assert_not_called()
        finally:
            agent.RETRY_SETTINGS = original


class ConversationRepairTests(unittest.TestCase):
    def test_repairs_openai_tool_exchange_and_preserves_request_and_result(self):
        history = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "Please fix the bug."},
            {"role": "assistant", "tool_calls": [{
                "id": "call-1", "type": "function",
                "function": {"name": "read_file", "arguments": "{bad json"}
            }]},
            {"role": "tool", "tool_call_id": "call-1", "name": "read_file",
             "content": "source code here"},
        ]
        repaired, removed = agent.repair_tool_history(history)
        self.assertEqual(removed, 2)
        self.assertEqual(repaired[1]["content"], "Please fix the bug.")
        self.assertIn("source code here", repaired[-1]["content"])
        self.assertFalse(any(item.get("role") == "tool" for item in repaired))
        self.assertFalse(any(item.get("tool_calls") for item in repaired))

    def test_repairs_cohere_thinking_block_with_empty_tool_calls(self):
        history = [
            {"role": "user", "content": "Continue the task."},
            {"role": "assistant", "tool_calls": [], "content": [
                {"type": "thinking", "thinking": "private reasoning"}
            ]},
        ]
        repaired, removed = agent.repair_tool_history(history)
        self.assertEqual(removed, 0)
        self.assertEqual(repaired[0]["content"], "Continue the task.")
        assistant = repaired[1]
        self.assertEqual(assistant["role"], "assistant")
        self.assertIsInstance(assistant["content"], str)
        self.assertNotIn("tool_calls", assistant)
        self.assertNotIn("thinking", assistant["content"])

    def test_outgoing_history_sanitizes_cohere_thinking_even_without_recovery(self):
        history = [
            {"role": "user", "content": "Continue."},
            {"role": "assistant", "tool_calls": [], "content": [
                {"type": "thinking", "thinking": "private reasoning"}
            ]},
        ]
        outgoing = agent.sanitize_openai_compatible_history(history)
        self.assertEqual(outgoing[1]["role"], "assistant")
        self.assertIsInstance(outgoing[1]["content"], str)
        self.assertNotIn("thinking", outgoing[1]["content"])
        self.assertNotIn("tool_calls", outgoing[1])
        # Sanitizing the outbound copy must not mutate the agent's history.
        self.assertIsInstance(history[1]["content"], list)
        self.assertEqual(history[1]["tool_calls"], [])

    def test_outgoing_history_preserves_valid_tool_calls(self):
        history = [{
            "role": "assistant",
            "tool_calls": [{"id": "c1", "type": "function"}],
            "content": [{"type": "thinking", "thinking": "private"},
                        {"type": "text", "text": "Calling tool."}],
        }]
        outgoing = agent.sanitize_openai_compatible_history(history)
        self.assertEqual(outgoing[0]["content"], "Calling tool.")
        self.assertEqual(len(outgoing[0]["tool_calls"]), 1)

    def test_keeps_visible_text_and_drops_thinking_blocks(self):
        history = [{"role": "assistant", "tool_calls": [], "content": [
            {"type": "thinking", "thinking": "private reasoning"},
            {"type": "text", "text": "I found the issue."},
        ]}]
        repaired, _ = agent.repair_tool_history(history)
        self.assertEqual(repaired[0]["content"], "I found the issue.")
        self.assertNotIn("tool_calls", repaired[0])

    def test_repairs_anthropic_tool_use_result_blocks(self):
        history = [
            {"role": "user", "content": "Do the task"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "I will inspect the file."},
                {"type": "tool_use", "id": "toolu-1", "name": "read_file", "input": {"path": "x"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu-1", "content": "file contents"}
            ]},
        ]
        repaired, removed = agent.repair_tool_history(history)
        self.assertEqual(removed, 2)
        self.assertTrue(any("I will inspect" in str(item.get("content")) for item in repaired))
        self.assertTrue(any("file contents" in str(item.get("content")) for item in repaired))
        self.assertFalse(any(
            isinstance(item.get("content"), list) and any(
                isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result")
                for block in item["content"]
            ) for item in repaired
        ))

    def test_repeated_provider_rejection_stops_without_resending_same_history(self):
        original_provider = getattr(agent, "PROVIDER", None)
        original_retry = agent.RETRY_SETTINGS
        agent.PROVIDER = "kilo-free"
        agent.RETRY_SETTINGS = dict(original_retry)
        agent.RETRY_SETTINGS["invalid_tool_call_recovery"] = dict(
            original_retry["invalid_tool_call_recovery"]
        )
        agent.RETRY_SETTINGS["invalid_tool_call_recovery"].update({
            "enabled": True,
            "max_recovery_attempts": 3,
            "repair_conversation_history": True,
            "stop_on_repeated_failure": True,
        })
        history = [{"role": "system", "content": "system"}]
        calls = []

        def always_reject(function, messages):
            calls.append([dict(item) for item in messages])
            raise agent.APIRequestError(
                "invalid tool call provided", status_code=400,
                invalid_tool_call=True
            )

        try:
            with mock.patch.object(agent, "run_interruptible_call", side_effect=always_reject), \
                 mock.patch.object(agent.Spinner, "start"), \
                 mock.patch.object(agent.Spinner, "stop"), \
                 mock.patch("builtins.print"):
                with self.assertRaises(agent.InvalidToolCallRecoveryFailure):
                    agent._ask_model_impl(history, "Please do this")
            self.assertEqual(len(calls), 2)
            self.assertTrue(any("Please do this" in str(item.get("content")) for item in calls[1]))
        finally:
            agent.RETRY_SETTINGS = original_retry
            if original_provider is None:
                delattr(agent, "PROVIDER")
            else:
                agent.PROVIDER = original_provider

    def test_recovery_regenerates_with_repaired_history(self):
        original_provider = getattr(agent, "PROVIDER", None)
        original_retry = agent.RETRY_SETTINGS
        agent.PROVIDER = "kilo-free"
        agent.RETRY_SETTINGS = dict(original_retry)
        agent.RETRY_SETTINGS["invalid_tool_call_recovery"] = dict(
            original_retry["invalid_tool_call_recovery"]
        )
        agent.RETRY_SETTINGS["invalid_tool_call_recovery"].update({
            "enabled": True,
            "max_recovery_attempts": 2,
            "repair_conversation_history": True,
            "stop_on_repeated_failure": True,
        })
        history = [
            {"role": "system", "content": "system"},
            {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "previous result"},
        ]
        seen_histories = []

        def fake_interruptible(function, messages):
            seen_histories.append([dict(item) for item in messages])
            if len(seen_histories) == 1:
                raise agent.APIRequestError(
                    "invalid tool call provided", status_code=400,
                    invalid_tool_call=True
                )
            return {"choices": [{"message": {"role": "assistant", "content": "Recovered response"}}]}

        try:
            with mock.patch.object(agent, "run_interruptible_call", side_effect=fake_interruptible), \
                 mock.patch.object(agent.Spinner, "start"), \
                 mock.patch.object(agent.Spinner, "stop"), \
                 mock.patch("builtins.print"):
                agent._ask_model_impl(history, "Please continue the task")
            self.assertEqual(len(seen_histories), 2)
            second = seen_histories[1]
            self.assertFalse(any(item.get("tool_calls") for item in second))
            self.assertFalse(any(item.get("role") == "tool" for item in second))
            self.assertTrue(any("Please continue the task" in str(item.get("content")) for item in second))
            self.assertTrue(any("previous result" in str(item.get("content")) for item in second))
            self.assertEqual(history[-1]["content"], "Recovered response")
        finally:
            agent.RETRY_SETTINGS = original_retry
            if original_provider is None:
                delattr(agent, "PROVIDER")
            else:
                agent.PROVIDER = original_provider


if __name__ == "__main__":
    unittest.main()
