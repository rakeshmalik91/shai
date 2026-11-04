import json
import tempfile
import unittest
from pathlib import Path

from modules.conversation_history import ConversationHistory


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


if __name__ == "__main__":
    unittest.main()
