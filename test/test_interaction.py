import pathlib
import sys
import unittest
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.engine.interaction import (
    InteractionHandler, DefaultCliInteractionHandler,
    set_interaction_handler, get_interaction_handler
)
from modules.engine import agent_runtime as engine


class CustomGuiHandler(InteractionHandler):
    def __init__(self):
        self.confirm_calls = []
        self.risky_calls = []
        self.choice_calls = []
        self.next_confirm = True
        self.next_risky = True
        self.next_choice = "2"

    def confirm_action(self, prompt, category=None, tool=None):
        self.confirm_calls.append((prompt, category, tool))
        return self.next_confirm

    def confirm_risky_action(self, prompt, category=None, tool=None):
        self.risky_calls.append((prompt, category, tool))
        return self.next_risky

    def select_choice(self, prompt, choices, default=None):
        self.choice_calls.append((prompt, choices, default))
        return self.next_choice

    def confirm_os_command(self, preview):
        return self.next_confirm

    def request_git_whitelist_scope(self, command, args):
        return "1"


class InteractionHandlerTests(unittest.TestCase):
    def setUp(self):
        self.original_handler = get_interaction_handler()

    def tearDown(self):
        set_interaction_handler(self.original_handler)

    def test_set_invalid_handler_raises_type_error(self):
        with self.assertRaises(TypeError):
            set_interaction_handler("not_a_handler")

    def test_custom_handler_intercepts_confirm_action(self):
        gui = CustomGuiHandler()
        gui.next_confirm = True
        set_interaction_handler(gui)

        with mock.patch.object(engine, "approval_granted", return_value=False):
            result = engine.confirm_action("Apply patch?", category="file_edit", tool="patch_file")
        self.assertTrue(result)
        self.assertEqual(len(gui.confirm_calls), 1)
        self.assertEqual(gui.confirm_calls[0][1], "file_edit")
        self.assertEqual(gui.confirm_calls[0][2], "patch_file")

    def test_custom_handler_intercepts_risky_approval(self):
        gui = CustomGuiHandler()
        gui.next_risky = False
        set_interaction_handler(gui)

        with mock.patch.object(engine, "approval_granted", return_value=False):
            result = engine._explicit_file_approval("Approve large deletion?", category="file_truncate", tool="edit_file")
        self.assertFalse(result)
        self.assertEqual(len(gui.risky_calls), 1)
        self.assertEqual(gui.risky_calls[0][1], "file_truncate")

    def test_os_command_permission_menu_returns_selected_scope(self):
        handler = DefaultCliInteractionHandler()
        with mock.patch.object(handler, "_prompt", return_value="s"):
            self.assertEqual(handler.request_os_command_permission("git status", "status"), "subcommand")

    def test_os_command_permission_menu_rejects_subcommand_when_unavailable(self):
        handler = DefaultCliInteractionHandler()
        with mock.patch.object(handler, "_prompt", side_effect=["s", "e"]):
            self.assertEqual(handler.request_os_command_permission("python3 -c print(1)", None), "exact")


    def test_os_command_permission_menu_shows_actual_permission_targets(self):
        handler = DefaultCliInteractionHandler()
        output = []
        with mock.patch("builtins.print", side_effect=lambda *args, **kwargs: output.append(" ".join(map(str, args)))):
            with mock.patch.object(handler, "_prompt", return_value="a"):
                self.assertEqual(
                    handler.request_os_command_permission("rd /s /q test_dir", None),
                    "once"
                )
        rendered = "\\n".join(output)
        self.assertIn("Allow command permanently (rd)", rendered)
        self.assertIn("Allow exact command permanently (rd /s /q test_dir)", rendered)
        self.assertNotIn("e.g.", rendered)
        self.assertNotIn("Allow subcommand permanently", rendered)

    def test_os_command_permission_menu_shows_actual_subcommand_target(self):
        handler = DefaultCliInteractionHandler()
        output = []
        with mock.patch("builtins.print", side_effect=lambda *args, **kwargs: output.append(" ".join(map(str, args)))):
            with mock.patch.object(handler, "_prompt", return_value="s"):
                self.assertEqual(handler.request_os_command_permission("git push origin main", "push"), "subcommand")
        rendered = "\\n".join(output)
        self.assertIn("Allow command permanently (git)", rendered)
        self.assertIn("Allow subcommand permanently (git push)", rendered)
        self.assertIn("Allow exact command permanently (git push origin main)", rendered)

    def test_custom_handler_intercepts_git_choice(self):
        gui = CustomGuiHandler()
        gui.next_choice = "2"
        set_interaction_handler(gui)

        choice = get_interaction_handler().select_choice("Choose [A/B]: ", ["1", "2"], default="1")
        self.assertEqual(choice, "2")
        self.assertEqual(len(gui.choice_calls), 1)


if __name__ == "__main__":
    unittest.main()
