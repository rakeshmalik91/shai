import io
import pathlib
import sys
import unittest
from unittest.mock import patch

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.live_status import LiveStatusPanel


class TTYBuffer(io.StringIO):
    def isatty(self):
        return True


class LiveStatusTests(unittest.TestCase):
    def test_tool_batch_rewrites_one_transient_line(self):
        stream = TTYBuffer()
        panel = LiveStatusPanel(stream=stream)
        with patch("modules.live_status.shutil.get_terminal_size") as terminal_size:
            terminal_size.return_value = type("Size", (), {"columns": 100, "lines": 30})()
            panel.start()
            panel.begin_tool_batch(3)
            for name in ("read_file", "search_files", "edit_file"):
                panel.tool_started(name, "agent.py")
                panel.tool_finished(name)
            panel.end_tool_batch()
            panel.stop()

        output = stream.getvalue()
        self.assertEqual(output.count("\r\033[2K"), 6)
        self.assertEqual(output.count("\n"), 1)
        self.assertNotIn("\033[1;", output)  # no reserved/frozen screen region
        self.assertIn("Tools 3/3: edit_file", output)
        self.assertEqual(panel.tool_count, 3)

    def test_context_and_task_updates_do_not_interrupt_chat_output(self):
        stream = TTYBuffer()
        panel = LiveStatusPanel(stream=stream)
        panel.start()
        panel.set_context("Context used: 12,000 / 1,000,000 (1.2%)")
        panel.update(task_plan=[{"title": "Inspect files", "status": "completed"}])
        self.assertEqual(stream.getvalue(), "")
        self.assertEqual(panel.context, "Context used: 12,000 / 1,000,000 (1.2%)")
        self.assertEqual(panel.tasks[0]["status"], "completed")

    def test_non_tty_does_not_emit_status_spam(self):
        stream = io.StringIO()
        panel = LiveStatusPanel(stream=stream)
        panel.start()
        panel.begin_tool_batch(2)
        panel.tool_started("read_file", "agent.py")
        panel.tool_finished("read_file")
        panel.tool_started("search_files", "modules")
        panel.tool_finished("search_files")
        panel.end_tool_batch()
        panel.stop()
        self.assertEqual(stream.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
