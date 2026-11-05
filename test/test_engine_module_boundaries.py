"""Regression tests for extracted engine-domain modules."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

class EngineModuleBoundaryTests(unittest.TestCase):
    def test_all_domain_modules_import(self):
        from modules.engine import (
            configuration, project_inspection, task_plan, git_operations,
            file_editing, provider_api, tool_execution, tool_presentation,
            history_recovery, model_orchestration,
        )
        for module in (
            configuration, project_inspection, task_plan, git_operations,
            file_editing, provider_api, tool_execution, tool_presentation,
            history_recovery, model_orchestration,
        ):
            self.assertTrue(callable(module.install))

    def test_runtime_installs_public_functions(self):
        from modules.engine import agent_runtime as runtime
        expected = {
            "load_config": "configuration",
            "read_file": "project_inspection",
            "update_task_plan": "task_plan",
            "git_status": "git_operations",
            "patch_file": "file_editing",
            "api_request": "provider_api",
            "execute_tool": "tool_execution",
            "display_tool_errors": "tool_presentation",
            "repair_tool_history": "history_recovery",
            "ask_model": "model_orchestration",
        }
        for function, _owner in expected.items():
            with self.subTest(function=function):
                self.assertTrue(callable(getattr(runtime, function)))

    def test_engine_runtime_remains_compatibility_surface(self):
        import agent
        from modules.engine import agent_runtime
        self.assertIs(agent, agent_runtime)
        self.assertTrue(callable(agent.main))
        self.assertTrue(callable(agent.ask_model))

    def test_domain_source_is_not_left_in_runtime(self):
        runtime_source = (ROOT / "modules/engine/agent_runtime.py").read_text()
        for function in (
            "def load_config(", "def read_file(", "def update_task_plan(",
            "def git_status(", "def patch_file(", "def api_request(",
            "def execute_tool(", "def ask_model(",
        ):
            with self.subTest(function=function):
                self.assertNotIn(function, runtime_source)

if __name__ == "__main__":
    unittest.main()
