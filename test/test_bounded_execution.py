import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent


class BoundedOutputCaptureTests(unittest.TestCase):
    def test_small_output_is_preserved_exactly(self):
        capture = agent.BoundedOutputCapture(32, 8, 8)
        capture.feed(b"hello world")
        self.assertEqual(capture.get_bytes(), b"hello world")
        self.assertFalse(capture.truncated)

    def test_large_output_keeps_head_and_tail(self):
        capture = agent.BoundedOutputCapture(16, 4, 4)
        capture.feed(b"abcdefghijklmnopqrstuvwx")
        self.assertEqual(capture.get_bytes(), b"abcd" + b"uvwx")
        self.assertTrue(capture.truncated)
        self.assertEqual(capture.total_bytes, 24)

    def test_large_command_output_is_bounded(self):
        command = [
            sys.executable, "-S", "-c",
            "import sys; sys.stdout.write('A'*200000); "
            "sys.stderr.write('B'*50000)"
        ]
        settings = dict(agent.TEST_EXECUTION)
        settings.update({
            "max_stdout_bytes": 1024,
            "max_stderr_bytes": 512,
            "preserve_output_head_bytes": 64,
            "preserve_output_tail_bytes": 64,
            "max_output_bytes": 1536,
            "terminate_child_processes": True
        })
        result = agent.run_bounded_interruptible_command(
            command, Path.cwd(), 10, settings
        )
        self.assertFalse(result["timed_out"])
        self.assertEqual(result["returncode"], 0)
        self.assertTrue(result["output_truncated"])
        self.assertEqual(result["stdout_total_bytes"], 200000)
        self.assertEqual(result["stderr_total_bytes"], 50000)
        self.assertLess(len(result["stdout"].encode("utf-8")), 300)
        self.assertLess(len(result["stderr"].encode("utf-8")), 300)

    def test_long_running_command_times_out(self):
        command = [sys.executable, "-S", "-c", "import time; time.sleep(10)"]
        settings = dict(agent.TEST_EXECUTION)
        settings["terminate_child_processes"] = True
        started = time.monotonic()
        result = agent.run_bounded_interruptible_command(
            command, Path.cwd(), 0.25, settings
        )
        elapsed = time.monotonic() - started
        self.assertTrue(result["timed_out"])
        self.assertLess(elapsed, 3.0)

    def test_stderr_and_stdout_are_both_drained(self):
        command = [
            sys.executable, "-S", "-c",
            "import sys; sys.stdout.write('out'); sys.stderr.write('err')"
        ]
        settings = dict(agent.TEST_EXECUTION)
        result = agent.run_bounded_interruptible_command(
            command, Path.cwd(), 5, settings
        )
        self.assertIn("out", result["stdout"])
        self.assertIn("err", result["stderr"])
        self.assertEqual(result["returncode"], 0)


if __name__ == "__main__":
    unittest.main()
