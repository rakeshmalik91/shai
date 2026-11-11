import json
import pathlib
import tempfile
import unittest

from modules.tools.registry import load_tool_functions, load_tool_registry


class ToolRegistryTests(unittest.TestCase):
    def test_registry_has_unique_named_tools(self):
        registry = load_tool_registry()
        self.assertEqual(len(registry), len(set(registry)))
        self.assertIn("os_command", registry)
        self.assertFalse(registry["os_command"].get("chat_available", False))
        self.assertIn("update_task_plan", registry)

    def test_agent_and_chat_declarations_come_from_registry(self):
        all_tools = load_tool_functions()
        chat_tools = load_tool_functions(chat_only=True)
        self.assertGreater(len(all_tools), len(chat_tools))
        self.assertTrue({"list_files", "read_file", "git_status"}.issubset(
            {item["function"]["name"] for item in chat_tools}
        ))
        self.assertIn("os_command", {item["function"]["name"] for item in all_tools})
        self.assertNotIn("os_command", {item["function"]["name"] for item in chat_tools})

    def test_each_schema_has_function_parameters(self):
        for item in load_tool_functions():
            function = item["function"]
            self.assertTrue(function.get("name"))
            self.assertEqual(function["parameters"].get("type"), "object")
            self.assertIn("properties", function["parameters"])
            self.assertTrue(isinstance(function.get("description"), str))
            self.assertGreater(len(function["description"]), 5)

    def test_load_tool_registry_missing_tools_array_raises(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(json.dumps({"not_tools": []}))
            temp_path = f.name
        try:
            with self.assertRaises(ValueError) as ctx:
                load_tool_registry(temp_path)
            self.assertIn("must contain a tools array", str(ctx.exception))
        finally:
            pathlib.Path(temp_path).unlink()

    def test_load_tool_registry_non_dict_entry_raises(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(json.dumps({"tools": ["not_a_dict"]}))
            temp_path = f.name
        try:
            with self.assertRaises(ValueError) as ctx:
                load_tool_registry(temp_path)
            self.assertIn("must contain a function object", str(ctx.exception))
        finally:
            pathlib.Path(temp_path).unlink()

    def test_load_tool_registry_missing_or_duplicate_name_raises(self):
        # Missing name
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(json.dumps({"tools": [{"function": {"parameters": {}}}]}))
            temp_path = f.name
        try:
            with self.assertRaises(ValueError) as ctx:
                load_tool_registry(temp_path)
            self.assertIn("missing or duplicate tool name", str(ctx.exception))
        finally:
            pathlib.Path(temp_path).unlink()

        # Duplicate name
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(json.dumps({"tools": [
                {"function": {"name": "dupe", "parameters": {}}},
                {"function": {"name": "dupe", "parameters": {}}}
            ]}))
            temp_path = f.name
        try:
            with self.assertRaises(ValueError) as ctx:
                load_tool_registry(temp_path)
            self.assertIn("missing or duplicate tool name", str(ctx.exception))
        finally:
            pathlib.Path(temp_path).unlink()

    def test_load_tool_functions_order_and_chat_filtering(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(json.dumps({"tools": [
                {"function": {"name": "tool_a", "parameters": {}}, "chat_available": True},
                {"function": {"name": "tool_b", "parameters": {}}, "chat_available": False},
                {"function": {"name": "tool_c", "parameters": {}}},
            ]}))
            temp_path = f.name
        try:
            all_fns = load_tool_functions(temp_path, chat_only=False)
            self.assertEqual([f["function"]["name"] for f in all_fns], ["tool_a", "tool_b", "tool_c"])

            chat_fns = load_tool_functions(temp_path, chat_only=True)
            self.assertEqual([f["function"]["name"] for f in chat_fns], ["tool_a"])
        finally:
            pathlib.Path(temp_path).unlink()


if __name__ == "__main__":
    unittest.main()
