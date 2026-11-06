import pathlib
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import agent


class PriorityToolTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp_dir.name)
        self.original_root = agent.ROOT
        agent.ROOT = self.root

    def tearDown(self):
        agent.ROOT = self.original_root
        self.temp_dir.cleanup()

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_get_file_outline_lists_functions_and_methods(self):
        self.write("sample.py", "import os\n\nclass Worker:\n    def run(self):\n        pass\n\ndef start():\n    pass\n")
        result = agent.get_file_outline("sample.py")
        self.assertIn("class Worker", result)
        self.assertIn("method run", result)
        self.assertIn("function start", result)

    def test_get_file_outline_syntax_error_and_empty_file(self):
        self.write("broken.py", "def broken(:\n    pass\n")
        syntax_res = agent.get_file_outline("broken.py")
        self.assertIn("Syntax error", syntax_res)

        self.write("empty.py", "# only comments\n")
        empty_res = agent.get_file_outline("empty.py")
        self.assertIn("No top-level declarations found", empty_res)

        with self.assertRaises(ValueError):
            agent.get_file_outline("missing.py")

    def test_find_symbol_and_references(self):
        self.write("sample.py", "def target():\n    return 1\n\ndef caller():\n    return target()\n")
        self.assertIn("function target", agent.find_symbol("target", "."))
        self.assertIn("return target()", agent.find_references("target", "."))

    def test_find_symbol_edge_cases(self):
        self.assertIn("symbol must be a non-empty string", agent.find_symbol("", "."))
        self.assertIn("symbol must be a non-empty string", agent.find_symbol(None, "."))
        self.write("sample.py", "value = 42\n")
        self.assertIn("No Python definition found for 'missing'", agent.find_symbol("missing", "."))

    def test_find_references_edge_cases(self):
        self.assertIn("symbol must be a non-empty string", agent.find_references("", "."))
        self.assertIn("symbol must be a non-empty string", agent.find_references(None, "."))
        self.write("sample.py", "hello = 1\n")
        self.assertIn("No matches found", agent.find_references("nonexistent_symbol_xyz", "."))

    def test_get_project_map_and_test_map(self):
        self.write("agent.py", "def main(): pass\n")
        self.write("test/test_agent.py", "def test_main(): pass\n")
        self.assertIn("agent.py", agent.get_project_map("."))
        self.assertIn("test/test_agent.py", agent.get_test_map("."))

    def test_inspect_test_failure_extracts_assertion(self):
        result = agent.inspect_test_failure(
            "FAIL: test_example (test_demo.TestDemo)\nTraceback (most recent call last):\nAssertionError: expected 1"
        )
        self.assertIn("FAIL: test_example", result)
        self.assertIn("AssertionError", result)

    def test_inspect_test_failure_empty_output(self):
        result = agent.inspect_test_failure("Everything passed with 0 errors!")
        self.assertIn("No common test failure markers found", result)

    def test_update_task_plan_validates_and_formats(self):
        result = agent.update_task_plan([
            {"title": "Inspect code", "status": "completed"},
            {"title": "Run tests", "status": "in_progress"}
        ])
        self.assertIn("[x] Inspect code", result)
        self.assertIn("[~] Run tests", result)
        self.assertEqual(len(agent.TASK_PLAN), 2)

    def test_update_task_plan_input_validation(self):
        self.assertIn("steps must be a non-empty array", agent.update_task_plan("not a list"))
        self.assertIn("steps must be a non-empty array", agent.update_task_plan([]))
        self.assertIn("needs a non-empty title", agent.update_task_plan([{"status": "pending"}]))
        self.assertIn("needs a non-empty title", agent.update_task_plan([{"title": "", "status": "pending"}]))

    def test_replace_lines_uses_line_numbers_and_approval(self):
        self.write("sample.py", "first = 1\nsecond = 2\nthird = 3\n")
        with mock.patch.object(agent, "confirm_action", return_value=True), \
             mock.patch.object(agent, "print_change_preview"), \
             mock.patch.object(agent, "_atomic_replace_file", return_value=None):
            result = agent.replace_lines("sample.py", 2, 2, "second = 20")
        self.assertIn("Successfully replaced lines 2-2", result)

    def test_replace_lines_bounds_and_validations(self):
        self.write("sample.py", "line 1\nline 2\n")
        # end_line < start_line
        self.assertIn("greater than or equal to start_line", agent.replace_lines("sample.py", 2, 1, "x"))
        # start_line < 1
        self.assertIn("start_line must be a positive 1-based line number", agent.replace_lines("sample.py", 0, 1, "x"))
        # end_line exceeds total lines
        self.assertIn("exceeds file length", agent.replace_lines("sample.py", 1, 10, "x"))
        # missing file
        self.assertIn("Target file does not exist", agent.replace_lines("missing.py", 1, 1, "x"))
        # user rejects approval
        with mock.patch.object(agent, "confirm_action", return_value=False), \
             mock.patch.object(agent, "print_change_preview"):
            res = agent.replace_lines("sample.py", 1, 1, "line 1 changed")
            self.assertIn("User declined line-range replacement", res)

    def test_verify_changes_runs_compile_and_unittest(self):
        fake_result = {
            "timed_out": False, "returncode": 0,
            "stdout": "OK", "stderr": "",
            "duration_seconds": 0.1,
            "stdout_total_bytes": 2, "stderr_total_bytes": 0,
            "output_truncated": False
        }
        with mock.patch.object(agent, "confirm_action", return_value=True), \
             mock.patch.object(agent, "run_bounded_interruptible_command", return_value=fake_result) as runner:
            result = agent.verify_changes(".", "unittest")
        self.assertIn("compileall: PASS", result)
        self.assertIn("unittest: PASS", result)
        self.assertEqual(runner.call_count, 2)

    def test_patch_failure_recommends_replace_lines(self):
        self.write("sample.py", "value = 1\n")
        result = agent.patch_file("sample.py", "value=1", "value=2")
        self.assertIn("replace_lines", result)

    def test_patch_mismatch_is_a_tool_error_and_stops_repeated_retry(self):
        self.write("sample.py", "    value = 1\n")
        args = {"path": "sample.py", "old_text": "value=1", "new_text": "value=2"}
        tracker = {}
        first = agent.execute_tool_with_safeguards("patch_file", args, tracker)
        self.assertIn("error", first)
        self.assertEqual(first.get("suggested_tool"), "replace_lines")
        second = agent.execute_tool_with_safeguards("patch_file", args, tracker)
        self.assertIn("error", second)
        third = agent.execute_tool_with_safeguards("patch_file", args, tracker)
        self.assertTrue(third.get("stop_request"))
        self.assertIn("Do not repeat", first.get("instruction", ""))
        self.assertEqual((self.root / "sample.py").read_text(encoding="utf-8"), "    value = 1\n")

    def test_priority_tools_are_registered_and_have_schemas(self):
        expected = {
            "replace_lines", "get_file_outline", "find_symbol",
            "find_references", "verify_changes", "inspect_test_failure",
            "get_project_map", "get_test_map", "update_task_plan"
        }
        self.assertTrue(expected.issubset(set(agent.TOOL_DISPATCH)))
        declared = {item["function"]["name"] for item in agent.FUNCTIONS}
        self.assertTrue(expected.issubset(declared))


if __name__ == "__main__":
    unittest.main()
