import json
import pathlib
import sys
import unittest
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
PROJECT_ROOT_STRING = str(PROJECT_ROOT)
if PROJECT_ROOT_STRING in sys.path:
    sys.path.remove(PROJECT_ROOT_STRING)
sys.path.insert(0, PROJECT_ROOT_STRING)

# Avoid picking up an unrelated agent.py from a parent working directory.
_existing_agent = sys.modules.get("agent")
if _existing_agent is not None:
    _loaded_from = pathlib.Path(getattr(_existing_agent, "__file__", "")).resolve()
    if _loaded_from.parent != PROJECT_ROOT:
        del sys.modules["agent"]

import agent
from modules.agent_safeguards import (
    bound_tool_result, compact_conversation_history, validate_tool_arguments,
    ToolArgumentFailureTracker, update_no_progress, read_file_cache_marker,
    tool_call_signature, format_tool_call_for_display
)


class AgentSafeguardModuleTests(unittest.TestCase):
    def test_tool_result_is_bounded_and_keeps_both_ends(self):
        value = {"output": "BEGIN" + ("x" * 2000) + "END"}
        rendered = bound_tool_result(value, 300)
        self.assertLessEqual(len(rendered), 300)
        self.assertIn("BEGIN", rendered)
        self.assertIn("END", rendered)
        self.assertIn("TRUNCATED", rendered)

    def test_compaction_preserves_latest_user_request_and_caps_results(self):
        history = [{"role": "system", "content": "system"}]
        for turn in range(8):
            history.append({"role": "user", "content": "request {}".format(turn)})
            for call in range(5):
                call_id = "{}-{}".format(turn, call)
                history.append({
                    "role": "assistant",
                    "content": "explanation " * 20,
                    "tool_calls": [{
                        "id": call_id,
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"}
                    }]
                })
                history.append({
                    "role": "tool", "tool_call_id": call_id,
                    "content": "x" * 3000
                })
            history.append({"role": "assistant", "content": "completed"})
        compacted = compact_conversation_history(
            history, max_chars=4000, max_tool_result_chars=400,
            keep_recent_turns=2, max_recent_tool_cycles=2
        )
        self.assertTrue(any(
            item.get("role") == "user" and item.get("content") == "request 7"
            for item in compacted
        ))
        self.assertLess(len(json.dumps(compacted)), 4000)
        for item in compacted:
            if item.get("role") == "tool":
                self.assertLessEqual(len(item.get("content", "")), 400)

    def test_compaction_keeps_anthropic_tool_result_pair_together(self):
        history = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "old request"},
            {"role": "assistant", "content": "old context " * 1000},
            {"role": "user", "content": "do work"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a"}}
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "result"}
            ]},
            {"role": "assistant", "content": [{"type": "text", "text": "done"}]}
        ]
        compacted = compact_conversation_history(
            history, max_chars=4000, max_tool_result_chars=1000,
            keep_recent_turns=1, max_recent_tool_cycles=1
        )
        self.assertTrue(any(
            item.get("role") == "user" and item.get("content") == "do work"
            for item in compacted
        ))
        assistant_tool = next(
            item for item in compacted
            if item.get("role") == "assistant"
            and isinstance(item.get("content"), list)
            and item["content"][0].get("type") == "tool_use"
        )
        tool_result = next(
            item for item in compacted
            if item.get("role") == "user"
            and isinstance(item.get("content"), list)
            and item["content"][0].get("type") == "tool_result"
        )
        self.assertEqual(assistant_tool["content"][0]["type"], "tool_use")
        self.assertEqual(tool_result["content"][0]["type"], "tool_result")

    def test_tool_argument_schema_rejects_unknown_parameters(self):
        error = validate_tool_arguments(
            "read_file", {"path": "README.md", "offset": 0, "limit": 100, "query": "bad"},
            agent.FUNCTIONS, reject_unknown=True
        )
        self.assertIn("Unexpected argument", error)

    def test_tool_argument_schema_checks_required_and_types(self):
        missing = validate_tool_arguments("read_file", {}, agent.FUNCTIONS)
        wrong_type = validate_tool_arguments(
            "read_file", {"path": 123, "offset": 0, "limit": 100}, agent.FUNCTIONS
        )
        self.assertIn("Missing required", missing)
        self.assertIn("must be string", wrong_type)

    def test_patch_file_is_registered_for_execution(self):
        self.assertIn("patch_file", agent.TOOL_DISPATCH)

    def test_handle_tool_selection_rejects_bad_arguments_before_execution(self):
        with mock.patch.object(agent, "patch_file") as patch:
            result, should_abort = agent.handle_tool_selection(
                "patch_file", {"path": "a.py", "old_text": "x"},
                {}, {"count": 0}
            )
        self.assertFalse(should_abort)
        self.assertIn("error", result)
        patch.assert_not_called()

    def test_truncated_provider_response_stops_without_retry_loop(self):
        original_provider = agent.PROVIDER
        original_settings = agent.AGENT_SAFEGUARDS
        agent.PROVIDER = "kilo-free"
        agent.AGENT_SAFEGUARDS = dict(original_settings)
        history = [{"role": "system", "content": "system"}]
        response = {"choices": [{
            "finish_reason": "length",
            "message": {"role": "assistant", "content": None}
        }]}
        try:
            with mock.patch.object(agent, "run_interruptible_call", return_value=response) as call, \
                 mock.patch.object(agent, "display_reasoning_summary"), \
                 mock.patch.object(agent, "Spinner") as spinner, \
                 mock.patch("builtins.print"):
                spinner.return_value.start.return_value = None
                spinner.return_value.stop.return_value = None
                agent._ask_model_impl(history, "make a change")
            self.assertEqual(call.call_count, 1)
            self.assertEqual(len(history), 3)
        finally:
            agent.PROVIDER = original_provider
            agent.AGENT_SAFEGUARDS = original_settings

    def test_repeated_successful_tool_batch_does_not_stop_progress(self):
        original_provider = agent.PROVIDER
        original_settings = agent.AGENT_SAFEGUARDS
        original_iterations = agent.MAX_ITERATIONS
        agent.PROVIDER = "kilo-free"
        agent.MAX_ITERATIONS = 20
        agent.AGENT_SAFEGUARDS = dict(original_settings)
        agent.AGENT_SAFEGUARDS.update({
            "max_consecutive_no_progress_iterations": 2,
            "max_tool_calls_per_request": 20
        })
        response = {"choices": [{
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": "Checking.",
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "list_files", "arguments": "{\"path\":\".\"}"}
                }]
            }
        }]}
        history = [{"role": "system", "content": "system"}]
        try:
            with mock.patch.object(agent, "run_interruptible_call", return_value=response) as call, \
                 mock.patch.object(agent, "display_reasoning_summary"), \
                 mock.patch.object(agent, "handle_tool_selection", return_value=({"ok": True}, False)), \
                 mock.patch.object(agent, "Spinner") as spinner, \
                 mock.patch("builtins.print"):
                spinner.return_value.start.return_value = None
                spinner.return_value.stop.return_value = None
                agent._ask_model_impl(history, "inspect project")
            self.assertEqual(call.call_count, 20)
        finally:
            agent.PROVIDER = original_provider
            agent.AGENT_SAFEGUARDS = original_settings
            agent.MAX_ITERATIONS = original_iterations

    def test_read_file_supports_offset_limit_and_continuation(self):
        import tempfile
        from pathlib import Path
        original_root = agent.ROOT
        original_settings = agent.AGENT_SAFEGUARDS
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "sample.txt").write_text(
                "one\ntwo\nthree\nfour\n", encoding="utf-8"
            )
            agent.ROOT = Path(directory)
            agent.AGENT_SAFEGUARDS = dict(original_settings)
            agent.AGENT_SAFEGUARDS.update({
                "default_read_file_lines": 2,
                "max_read_file_lines": 500,
                "max_read_file_chars": 4000
            })
            try:
                result = agent.read_file("sample.txt", offset=1, limit=2)
                self.assertIn("Lines 2-3 of 4", result)
                self.assertIn("two\nthree\n", result)
                self.assertNotIn("one\n", result)
                self.assertIn("offset=3", result)
            finally:
                agent.ROOT = original_root
                agent.AGENT_SAFEGUARDS = original_settings

    def test_read_file_schema_accepts_offset_and_limit(self):
        self.assertIsNone(validate_tool_arguments(
            "read_file", {"path": "a.py", "offset": 300, "limit": 100},
            agent.FUNCTIONS
        ))
        self.assertIn("offset", validate_tool_arguments(
            "read_file", {"path": "a.py"}, agent.FUNCTIONS
        ))
        self.assertIn("at least 0", validate_tool_arguments(
            "read_file", {"path": "a.py", "offset": -1, "limit": 100}, agent.FUNCTIONS
        ))

    def test_invalid_arguments_do_not_permanently_disable_tool(self):
        tracker = ToolArgumentFailureTracker(max_failures=2)
        with mock.patch.object(agent, "execute_tool", return_value={"result": "ok"}) as execute:
            first, abort_first = agent.handle_tool_selection(
                "read_file", {"path": "x", "offset": 0, "limit": 100, "query": "unsupported"},
                {}, {"count": 0}, invalid_args_tracker=tracker
            )
            second, abort_second = agent.handle_tool_selection(
                "read_file", {"path": "x", "offset": 0, "limit": 100, "query": "unsupported"},
                {}, {"count": 0}, invalid_args_tracker=tracker
            )
            third, abort_third = agent.handle_tool_selection(
                "read_file", {"path": "x", "offset": 0, "limit": 100}, {}, {"count": 0},
                invalid_args_tracker=tracker
            )
        self.assertFalse(abort_first)
        self.assertFalse(abort_second)
        self.assertFalse(abort_third)
        self.assertIn("Validation warning 1/2", first["instruction"])
        self.assertFalse(second["tool_disabled_for_request"])
        self.assertEqual(third["result"], "ok")
        execute.assert_called_once()

    def test_repeated_read_file_executes_instead_of_synthetic_suppression(self):
        args = {"path": "sample.py", "offset": 0, "limit": 100}
        signature = agent._tool_call_signature("read_file", args)
        tracker = {signature: {
            "count": 1, "result": {"result": "old contents"},
            "parameters": dict(args)
        }}
        with mock.patch.object(
            agent, "execute_tool", return_value={"result": "fresh file contents"}
        ) as execute:
            result = agent.execute_tool_with_safeguards(
                "read_file", args, tracker,
                history=[{"role": "tool", "content": "old contents"}]
            )
        self.assertEqual(result["result"], "fresh file contents")
        self.assertNotIn("duplicate_read_suppressed", result)
        execute.assert_called_once_with("read_file", args)

    def test_file_edit_does_not_affect_read_execution(self):
        signature = agent._tool_call_signature(
            "read_file", {"path": "sample.py", "offset": 0, "limit": 100}
        )
        tracker = {signature: {"count": 1, "parameters": {
            "path": "sample.py", "offset": 0, "limit": 100
        }}}
        with mock.patch.object(agent, "execute_tool", return_value={"result": "updated"}) as execute:
            result = agent.execute_tool_with_safeguards(
                "edit_file", {"path": "sample.py"}, tracker, history=[]
            )
        self.assertEqual(result["result"], "updated")
        execute.assert_called_once()

    def test_parameterized_signatures_distinguish_read_file_pages(self):
        first = tool_call_signature(
            "read_file", {"path": "chat.py", "offset": 0, "limit": 100}
        )
        second = tool_call_signature(
            "read_file", {"path": "chat.py", "offset": 100, "limit": 100}
        )
        same_with_default = tool_call_signature("read_file", {"path": "chat.py"})
        self.assertNotEqual(first, second)
        self.assertEqual(first, same_with_default)

    def test_tool_call_display_shows_pagination_parameters(self):
        rendered = format_tool_call_for_display(
            "read_file", {"path": "chat.py", "offset": 200, "limit": 100}
        )
        self.assertIn("read_file", rendered)
        self.assertIn('"offset":200', rendered)
        self.assertIn('"limit":100', rendered)

    def test_no_progress_distinguishes_failed_argument_variations_from_success(self):
        recent = []
        count, repeated = update_no_progress(
            recent, ("read_file", "bad-args-1"), 0, True, 0
        )
        self.assertEqual(count, 1)
        self.assertFalse(repeated)
        count, repeated = update_no_progress(
            recent, ("read_file", "bad-args-2"), count, True, 0
        )
        self.assertEqual(count, 2)
        self.assertFalse(repeated)
        count, repeated = update_no_progress(
            recent, ("read_file", "offset-200"), count, False, 1
        )
        self.assertEqual(count, 0)
        self.assertFalse(repeated)

    def test_repeated_successful_batch_resets_no_progress(self):
        recent = [("read_file", "page-1")]
        count, repeated = update_no_progress(
            recent, ("read_file", "page-1"), 2, False, 1
        )
        self.assertTrue(repeated)
        self.assertEqual(count, 0)

    def test_compaction_preserves_summary_of_completed_tool_work(self):
        history = [{"role": "system", "content": "system"},
                   {"role": "user", "content": "inspect"}]
        for index in range(10):
            call_id = "c{}".format(index)
            history.append({"role": "assistant", "content": "", "tool_calls": [{
                "id": call_id, "type": "function",
                "function": {"name": "read_file", "arguments": "{}"}
            }]})
            history.append({"role": "tool", "tool_call_id": call_id,
                            "content": json.dumps({"result": ("Read file part {} ".format(index)) + ("x" * 900)})})
        history.append({"role": "assistant", "content": "Continue."})
        compacted = compact_conversation_history(
            history, max_chars=4000, max_tool_result_chars=400,
            keep_recent_turns=1, max_recent_tool_cycles=1
        )
        joined = "\n".join(str(item.get("content", "")) for item in compacted)
        self.assertIn("Completed work", joined)
        self.assertIn("Read file part", joined)

    def test_read_file_can_be_reread_even_if_previous_result_is_in_history(self):
        marker = read_file_cache_marker("sample.py", 0, 100)
        tracker = {
            agent._tool_call_signature("read_file", {"path": "sample.py"}): {
                "count": 3,
                "result": {"result": "old file contents"},
                "cache_marker": marker
            }
        }
        with mock.patch.object(agent, "execute_tool", return_value={"result": "fresh chunk"}) as execute:
            result = agent.execute_tool_with_safeguards(
                "read_file", {"path": "sample.py"}, tracker, history=[]
            )
        self.assertEqual(result["result"], "fresh chunk")
        execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
