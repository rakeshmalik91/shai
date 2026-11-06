import unittest

from modules.commands.shai_command import (
    load_command_registry,
    resolve_shai_command,
)
from modules.commands.registry import normalize_command


class ShaiCommandTests(unittest.TestCase):
    def test_registry_is_loaded_from_assets_json(self):
        registry = load_command_registry()
        self.assertGreater(len(registry["commands"]), 10)

    def test_clear_agent_commands(self):
        cases = {
            "Show my Git status": ("/status", "agent"),
            "please show my task list": ("/todo", "agent"),
            "Could you switch to read-only mode?": ("/read-only", "agent"),
            "commit and push": ("/commit-push", "agent"),
            "generate a commit message": ("/commit --message-only", "agent"),
        }
        for phrase, (expected, mode) in cases.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    resolve_shai_command(phrase, mode), expected
                )

    def test_chat_status_is_mode_specific(self):
        self.assertEqual(
            resolve_shai_command("show status", "chat"), "/status"
        )
        self.assertIsNone(
            resolve_shai_command("show status", "agent")
        )
        self.assertEqual(
            resolve_shai_command("show git status", "agent"), "/status"
        )

    def test_negated_and_meta_requests_do_not_trigger(self):
        for phrase in (
            "don't commit yet",
            "what does git status do",
            "should I push my commits",
            "explain how read-only mode works",
        ):
            with self.subTest(phrase=phrase):
                self.assertIsNone(
                    resolve_shai_command(phrase, "agent")
                )

    def test_ambiguous_or_argument_requiring_requests_are_not_guessed(self):
        for phrase in ("reset it", "switch project", "run the tests", "execute pytest"):
            with self.subTest(phrase=phrase):
                self.assertIsNone(
                    resolve_shai_command(phrase, "agent")
                )

    def test_commands_are_restricted_by_mode(self):
        self.assertIsNone(
            resolve_shai_command("show project files", "chat")
        )
        self.assertIsNone(
            resolve_shai_command("switch to code mode", "chat")
        )

    def test_slash_commands_are_not_reinterpreted(self):
        self.assertIsNone(
            resolve_shai_command("/status", "agent")
        )

    def test_slash_aliases_are_registry_backed(self):
        self.assertEqual(normalize_command("/ro"), "/read-only")
        self.assertEqual(normalize_command("/switch-model"), "/model")
        self.assertEqual(normalize_command("/task"), "/todo")
        self.assertEqual(normalize_command("/commit --message-only"),
                         "/commit --message-only")

    def test_legacy_exec_alias_normalizes_to_os_command(self):
        from modules.commands.registry import normalize_command
        self.assertEqual(
            normalize_command("/exec git status"),
            "/os-command git status"
        )


    def test_shai_command_can_be_disabled_in_config(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "shai_config.json"
            config_path.write_text(json.dumps({"shai_command": {"enabled": False}}))
            self.assertIsNone(resolve_shai_command(
                "show git status", "agent", config_path=config_path
            ))



if __name__ == "__main__":
    unittest.main()
