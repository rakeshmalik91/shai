import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from modules.conversation_history import ConversationHistory, input_without_history


class ConversationHistoryTests(unittest.TestCase):
    def test_mode_histories_are_separate_and_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            agent = ConversationHistory(root, "agent", "agent system")
            chat = ConversationHistory(root, "chat", "chat system")
            agent_messages = agent.load() + [
                {"role": "user", "content": "inspect"},
                {"role": "assistant", "tool_calls": [{"id": "call-1"}]},
                {"role": "tool", "tool_call_id": "call-1", "content": "[]"},
            ]
            chat_messages = chat.load() + [
                {"role": "user", "content": "hello"}
            ]
            agent.save(agent_messages)
            chat.save(chat_messages)

            self.assertEqual(agent.load(), agent_messages)
            self.assertEqual(chat.load(), chat_messages)

    def test_prompt_history_shared_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as root:
            agent = ConversationHistory(root, "agent", "system")
            chat = ConversationHistory(root, "chat", "system")
            agent.add_input("first")
            chat.add_input("second")
            agent.add_input("second")

            data = json.loads(
                (Path(root) / ".shai" / "history.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(data["input_history"], ["first", "second"])

    def test_selection_input_does_not_enter_readline_history(self):
        class FakeReadline:
            def __init__(self):
                self.items = []
                self.auto_history = True

            def get_auto_history(self):
                return self.auto_history

            def set_auto_history(self, enabled):
                self.auto_history = enabled

            def get_current_history_length(self):
                return len(self.items)

            def remove_history_item(self, index):
                del self.items[index]

        fake_readline = FakeReadline()

        def fake_input(prompt):
            if fake_readline.auto_history:
                fake_readline.items.append("2")
            return "2"

        with patch.dict("sys.modules", {"readline": fake_readline}):
            with patch("builtins.input", side_effect=fake_input):
                self.assertEqual(input_without_history("Choose: "), "2")

        self.assertEqual(fake_readline.items, [])
        self.assertTrue(fake_readline.auto_history)

    def test_command_execution_inputs_are_in_prompt_history(self):
        with tempfile.TemporaryDirectory() as root:
            store = ConversationHistory(root, "agent", "system")
            store.add_input("inspect the project")
            store.add_input("/exec git status")
            store.add_input("/os-command pwd")
            store.add_input("/status")

            data = json.loads(store.path.read_text(encoding="utf-8"))
            self.assertEqual(data["input_history"], [
                "inspect the project", "/exec git status",
                "/os-command pwd", "/status"
            ])

    def test_clear_preserves_prompt_and_other_mode_history(self):
        with tempfile.TemporaryDirectory() as root:
            agent = ConversationHistory(root, "agent", "agent system")
            chat = ConversationHistory(root, "chat", "chat system")
            agent.save(agent.load() + [{"role": "user", "content": "a"}])
            chat.save(chat.load() + [{"role": "user", "content": "c"}])
            agent.add_input("remember me")
            agent.clear()

            self.assertEqual(agent.load(), [
                {"role": "system", "content": "agent system"}
            ])
            self.assertEqual(len(chat.load()), 2)
            data = json.loads(
                (Path(root) / ".shai" / "history.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(data["input_history"], ["remember me"])

    def test_history_file_is_json_and_has_expected_path(self):
        with tempfile.TemporaryDirectory() as root:
            store = ConversationHistory(root, "agent", "system")
            store.save(store.load())
            self.assertEqual(
                store.path, Path(root) / ".shai" / "history.json"
            )
            self.assertTrue(store.path.is_file())
            self.assertEqual(json.loads(store.path.read_text())["version"], 1)
            self.assertFalse((Path(root) / ".shai" / ".gitignore").exists())

    def test_corrupt_history_file_recovers_to_empty(self):
        with tempfile.TemporaryDirectory() as root:
            history_file = Path(root) / ".shai" / "history.json"
            history_file.parent.mkdir(parents=True, exist_ok=True)
            history_file.write_text("CORRUPTED_JSON{:", encoding="utf-8")

            store = ConversationHistory(root, "agent", "default system")
            messages = store.load()
            self.assertEqual(messages, [{"role": "system", "content": "default system"}])

    def test_metadata_persistence_and_mode_isolation(self):
        with tempfile.TemporaryDirectory() as root:
            agent = ConversationHistory(root, "agent", "agent system")
            chat = ConversationHistory(root, "chat", "chat system")

            agent.save(agent.load(), provider="openrouter", model="claude")
            chat.save(chat.load(), provider="ollama", model="llama")

            self.assertEqual(agent.metadata().get("provider"), "openrouter")
            self.assertEqual(agent.metadata().get("model"), "claude")
            self.assertEqual(chat.metadata().get("provider"), "ollama")
            self.assertEqual(chat.metadata().get("model"), "llama")

    def test_add_input_ignores_whitespace_and_caps_history(self):
        with tempfile.TemporaryDirectory() as root:
            store = ConversationHistory(root, "agent", "system", max_input_history=3)
            store.add_input("")
            store.add_input("   ")
            store.add_input("one")
            store.add_input("two")
            store.add_input("three")
            store.add_input("four")

            data = json.loads(store.path.read_text(encoding="utf-8"))
            self.assertEqual(data["input_history"], ["two", "three", "four"])

    def test_load_returns_deep_copy(self):
        with tempfile.TemporaryDirectory() as root:
            store = ConversationHistory(root, "agent", "system")
            initial = store.load()
            initial.append({"role": "user", "content": "mutation"})

            fresh = store.load()
            self.assertEqual(len(fresh), 1)
            self.assertEqual(fresh[0]["role"], "system")


if __name__ == "__main__":
    unittest.main()
