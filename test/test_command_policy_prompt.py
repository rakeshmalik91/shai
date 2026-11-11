import unittest
from unittest.mock import patch

from modules.engine.agent_runtime import SYSTEM_PROMPT, FUNCTIONS, TOOL_DISPATCH
from modules.tools.registry import load_tool_registry
import modules.engine.agent_runtime as runtime


class CommandPolicyPromptTests(unittest.TestCase):
    def test_prompt_allows_model_os_command_with_configured_approval(self):
        prompt = SYSTEM_PROMPT.lower()
        self.assertIn("may execute operating-system commands", prompt)
        self.assertIn("model-facing `os_command`", prompt)
        self.assertIn("approval", prompt)
        self.assertIn("reserved", prompt)
        self.assertIn("human", prompt)

    def test_os_command_is_exposed_only_as_agent_tool(self):
        registry = load_tool_registry()
        self.assertIn("os_command", registry)
        self.assertFalse(registry["os_command"].get("chat_available", False))
        self.assertIn(
            "os_command",
            {item["function"]["name"] for item in FUNCTIONS},
        )
        self.assertIn("os_command", TOOL_DISPATCH)

    def test_manual_exec_skips_routine_approval_and_passes_manual_mode(self):
        captured = {}

        def fake_execute(*args, **kwargs):
            captured["confirm"] = kwargs["confirm"]
            captured["manual"] = kwargs.get("manual", False)
            return {"status": "completed", "exit_code": 0, "stdout": "", "stderr": ""}

        with (
            patch.object(runtime, "_load_os_command_settings", return_value={}),
            patch.object(runtime, "execute_os_command", side_effect=fake_execute),
        ):
            result = runtime.os_command("git", ["status"], manual=True)

        self.assertEqual(result["status"], "completed")
        self.assertTrue(captured["manual"])
        self.assertTrue(captured["confirm"]("test preview"))


if __name__ == "__main__":
    unittest.main()
