import unittest

from modules.tools.registry import load_tool_functions, load_tool_registry


class ToolRegistryTests(unittest.TestCase):
    def test_registry_has_unique_named_tools(self):
        registry = load_tool_registry()
        self.assertEqual(len(registry), len(set(registry)))
        self.assertIn("os_command", registry)
        self.assertIn("update_task_plan", registry)

    def test_agent_and_chat_declarations_come_from_registry(self):
        all_tools = load_tool_functions()
        chat_tools = load_tool_functions(chat_only=True)
        self.assertGreater(len(all_tools), len(chat_tools))
        self.assertTrue({"list_files", "read_file", "git_status"}.issubset(
            {item["function"]["name"] for item in chat_tools}
        ))
        self.assertNotIn("os_command", {item["function"]["name"] for item in chat_tools})

    def test_each_schema_has_function_parameters(self):
        for item in load_tool_functions():
            function = item["function"]
            self.assertTrue(function.get("name"))
            self.assertEqual(function["parameters"].get("type"), "object")


if __name__ == "__main__":
    unittest.main()
