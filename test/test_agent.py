import json
import pathlib
import sys
import tempfile
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

    def test_iteration_limit_is_read_from_config(self):
        self.write_config({"max_tool_call_iterations": 77})
        self.assertEqual(agent.load_max_tool_call_iterations(), 77)

    def test_iteration_limit_is_capped_to_prevent_unbounded_requests(self):
        self.write_config({"max_tool_call_iterations": 500})
        self.assertEqual(agent.load_max_tool_call_iterations(), 80)

    def test_invalid_iteration_limit_uses_default(self):
        self.write_config({"max_tool_call_iterations": True})
        with mock.patch("builtins.print"):
            self.assertEqual(
                agent.load_max_tool_call_iterations(),
                agent.DEFAULT_MAX_TOOL_CALL_ITERATIONS,
            )

    def test_yes_to_all_is_persisted_without_losing_other_settings(self):
        original = {
            "provider": "kilo-free",
            "model": "example:model:free",
            "max_tool_call_iterations": 100,
            "retry": {"max_retries": 5},
        }
        self.write_config(original)
        agent.save_yes_to_all(True)
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertTrue(saved["yes_to_all"])
        self.assertEqual(saved["retry"], original["retry"])
        self.assertEqual(saved["provider"], original["provider"])
        self.assertTrue(agent.load_yes_to_all())

    def test_retry_settings_validate_and_load(self):
        self.write_config({
            "retry": {
                "max_retries": 5,
                "initial_backoff_seconds": 0.25,
                "backoff_multiplier": 3,
                "retry_http_statuses": [429, 503, "bad"],
            }
        })
        settings = agent.load_retry_settings()
        self.assertEqual(settings["max_retries"], 5)
        self.assertEqual(settings["initial_backoff_seconds"], 0.25)
        self.assertEqual(settings["backoff_multiplier"], 3.0)
        self.assertEqual(settings["retry_http_statuses"], [429, 503])

    def test_tool_safeguards_are_configurable(self):
        self.write_config({
            "tool_safeguards": {
                "duplicate_call_limit": 4,
                "run_checks_timeout_seconds": 45,
            }
        })
        settings = agent.load_tool_safeguards()
        self.assertEqual(settings["duplicate_call_limit"], 4)
        self.assertEqual(settings["run_checks_timeout_seconds"], 45)


class PlanContinuationTests(unittest.TestCase):
    def setUp(self):
        self.original_plan = agent.TASK_PLAN
        agent.TASK_PLAN = [
            {"title": "Implement change", "status": "in_progress"},
            {"title": "Run tests", "status": "pending"},
        ]

    def tearDown(self):
        agent.TASK_PLAN = self.original_plan

    def test_text_only_response_continues_when_plan_is_incomplete(self):
        history = []
        self.assertTrue(agent._continue_incomplete_plan(history, 0, "stop"))
        self.assertEqual(history[-1]["role"], "user")
        self.assertIn("Implement change", history[-1]["content"])
        self.assertIn("Run tests", history[-1]["content"])

    def test_continuation_is_bounded(self):
        history = []
        self.assertFalse(agent._continue_incomplete_plan(
            history, agent.MAX_PLAN_CONTINUATIONS, "stop"
        ))
        self.assertEqual(history, [])

    def test_completed_plan_does_not_continue(self):
        agent.TASK_PLAN = [{"title": "Done", "status": "completed"}]
        self.assertFalse(agent._continue_incomplete_plan([], 0, "stop"))


class ToolSafetyTests(unittest.TestCase):
    def setUp(self):
        self.original_dispatch = dict(agent.TOOL_DISPATCH)
        self.original_safeguards = agent.TOOL_SAFEGUARDS
        agent.TOOL_SAFEGUARDS = dict(agent.DEFAULT_TOOL_SAFEGUARDS)
        agent.TOOL_SAFEGUARDS.update({
            "duplicate_call_detection": True,
            "duplicate_call_limit": 2,
            "repeat_call_warning": True,
            "repeat_call_warning_after": 2,
        })

    def tearDown(self):
        agent.TOOL_DISPATCH.clear()
        agent.TOOL_DISPATCH.update(self.original_dispatch)
        agent.TOOL_SAFEGUARDS = self.original_safeguards

    def test_tool_is_executed_once_per_call(self):
        function = mock.Mock(return_value="ok")
        agent.TOOL_DISPATCH["test_probe"] = function
        result = agent.execute_tool_with_safeguards(
            "test_probe", {"value": 1}, {}
        )
        self.assertEqual(result["result"], "ok")
        function.assert_called_once_with(value=1)

    def test_identical_calls_are_allowed_and_warn_with_parameters(self):
        function = mock.Mock(return_value="ok")
        agent.TOOL_DISPATCH["test_probe"] = function
        tracker = {}
        first = agent.execute_tool_with_safeguards(
            "test_probe", {"value": 1}, tracker
        )
        second = agent.execute_tool_with_safeguards(
            "test_probe", {"value": 1}, tracker
        )
        third = agent.execute_tool_with_safeguards(
            "test_probe", {"value": 1}, tracker
        )
        self.assertEqual(first["result"], "ok")
        self.assertIn("repeat_call_warning", second)
        self.assertIn("repeat_call_parameters", second)
        self.assertIn("repeat_call_warning", third)
        function.assert_has_calls([
            mock.call(value=1), mock.call(value=1), mock.call(value=1)
        ])
        self.assertEqual(function.call_count, 3)

    def test_edit_file_patch_arguments_are_routed_to_patch_file(self):
        patch = mock.Mock(return_value={"result": "patched"})
        original_patch = agent.TOOL_DISPATCH.get("patch_file")
        agent.TOOL_DISPATCH["patch_file"] = patch
        try:
            result, should_abort = agent.handle_tool_selection(
                "edit_file",
                {
                    "path": "sample.py",
                    "old_text": "before",
                    "new_text": "after",
                },
                {},
                {"count": 0},
            )
        finally:
            agent.TOOL_DISPATCH["patch_file"] = original_patch
        self.assertFalse(should_abort)
        patch.assert_called_once_with(
            path="sample.py", old_text="before", new_text="after"
        )
        self.assertEqual(result["tool_name_executed"], "patch_file")
        self.assertEqual(result["tool_name_normalized_from"], "edit_file")
        self.assertIn("compatibility_note", result)

    def test_tool_name_resolution_accepts_prefix_and_close_typo(self):
        self.assertEqual(
            agent.resolve_tool_name("functions.run_checks"), "run_checks"
        )
        self.assertEqual(agent.resolve_tool_name("run_check"), "run_checks")
        self.assertIsNone(agent.resolve_tool_name("invented_tool"))

    def test_invalid_tool_selection_has_bounded_recovery(self):
        original = agent.RETRY_SETTINGS
        agent.RETRY_SETTINGS = dict(original)
        agent.RETRY_SETTINGS.update({
            "invalid_tool_selection_enabled": True,
            "invalid_tool_selection_max_attempts": 2,
        })
        try:
            counter = {"count": 0}
            _, abort_first = agent.handle_tool_selection(
                "not_a_tool", {}, {}, counter
            )
            result, abort_second = agent.handle_tool_selection(
                "not_a_tool", {}, {}, counter
            )
            self.assertFalse(abort_first)
            self.assertTrue(abort_second)
            self.assertIn("allowed_tools", result)
        finally:
            agent.RETRY_SETTINGS = original

    def test_api_retries_transient_http_error(self):
        original = agent.RETRY_SETTINGS
        agent.RETRY_SETTINGS = dict(original)
        agent.RETRY_SETTINGS.update({
            "enabled": True,
            "max_retries": 1,
            "initial_backoff_seconds": 0,
            "max_backoff_seconds": 0,
            "jitter": False,
            "retry_http_statuses": [429],
        })
        try:
            with mock.patch.object(
                agent, "api_request",
                side_effect=[agent.APIRequestError("rate limited", status_code=429), {"ok": True}],
            ) as request, mock.patch.object(agent.time, "sleep") as sleep:
                result = agent.api_request_with_retries([])
            self.assertEqual(result, {"ok": True})
            self.assertEqual(request.call_count, 2)
            sleep.assert_called_once_with(0)
        finally:
            agent.RETRY_SETTINGS = original

    def test_interrupted_request_rolls_back_partial_history(self):
        history = [{"role": "system", "content": "system"}]
        with mock.patch.object(
            agent, "_ask_model_impl", side_effect=agent.IterationInterrupted()
        ), mock.patch("builtins.print"):
            agent.ask_model(history, "do work")
        self.assertEqual(len(history), 1)

    def test_provider_switch_history_discards_tool_records(self):
        history = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "tool result"},
            {"role": "assistant", "content": "hi"},
        ]
        cleaned = agent.history_for_new_provider(history)
        self.assertEqual([item["role"] for item in cleaned], ["system", "user", "assistant"])
        self.assertEqual(cleaned[-1]["content"], "hi")

    def test_search_files_accepts_single_file_path(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = pathlib.Path(root)
            target = root_path / "agent.py"
            target.write_text("def initialize_provider():\n    return True\n", encoding="utf-8")
            original_root = agent.ROOT
            try:
                agent.ROOT = root_path
                result = agent.search_files("initialize_provider", "agent.py")
                self.assertIn("agent.py:1:", result)
            finally:
                agent.ROOT = original_root

    def test_three_identical_tool_errors_stop_request(self):
        function = mock.Mock(return_value={"error": "Not a directory"})
        agent.TOOL_DISPATCH["test_failing_probe"] = function
        tracker = {}
        try:
            agent.execute_tool_with_safeguards("test_failing_probe", {"path": "agent.py"}, tracker)
            agent.execute_tool_with_safeguards("test_failing_probe", {"path": "agent.py"}, tracker)
            third = agent.execute_tool_with_safeguards("test_failing_probe", {"path": "agent.py"}, tracker)
            self.assertTrue(third.get("stop_request"))
            self.assertEqual(function.call_count, 3)
        finally:
            agent.TOOL_DISPATCH.pop("test_failing_probe", None)

    def test_kilo_schema_preserves_file_search_and_pagination_guidance(self):
        tools = agent.build_kilo_tools()
        by_name = {item["function"]["name"]: item["function"] for item in tools}
        search_description = by_name["search_files"]["description"]
        self.assertIn("single project file", search_description)
        read_description = by_name["read_file"]["description"]
        self.assertIn("next_offset", read_description)
        self.assertIn("offset", by_name["read_file"]["parameters"]["properties"])


if __name__ == "__main__":
    unittest.main()
