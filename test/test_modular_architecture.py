import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

class ModularArchitectureTests(unittest.TestCase):
    def test_legacy_entry_points_resolve_to_runtime_modules(self):
        import agent
        import chat
        from modules.engine import agent_runtime
        from modules.ui import chat_runtime
        self.assertIs(agent, agent_runtime)
        self.assertIs(chat, chat_runtime)

    def test_command_aliases_preserve_arguments(self):
        from modules.commands.registry import normalize_command
        self.assertEqual(normalize_command("/switch"), "/model")
        self.assertEqual(normalize_command("/ro"), "/read-only")
        self.assertEqual(normalize_command("/task"), "/todo")
        self.assertEqual(
            normalize_command("/project ~/src"), "/project ~/src"
        )


    def test_command_help_is_externalized(self):
        from modules.commands.help import HELP_TEXT
        self.assertIn("/commit-push", HELP_TEXT)
        self.assertIn("/read-only", HELP_TEXT)

    def test_spinner_is_a_ui_component(self):
        from modules.ui.spinner import Spinner
        from modules.ui.chat_runtime import Spinner as ChatSpinner
        self.assertIs(Spinner, ChatSpinner)

    def test_tool_facade_exports_split_implementations(self):
        from modules import tools
        from modules.tools.filesystem import read_file
        from modules.tools.git import git_status
        from modules.tools.inspection import get_project_map
        self.assertIs(tools.read_file, read_file)
        self.assertIs(tools.git_status, git_status)
        self.assertIs(tools.get_project_map, get_project_map)

    def test_tool_dispatch_contains_split_tool_functions(self):
        from modules.tools import TOOL_DISPATCH_READ_ONLY
        self.assertIn("read_file", TOOL_DISPATCH_READ_ONLY)
        self.assertIn("git_status", TOOL_DISPATCH_READ_ONLY)
        self.assertIn("get_project_map", TOOL_DISPATCH_READ_ONLY)

if __name__ == "__main__":
    unittest.main()
