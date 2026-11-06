import logging
import tempfile
import unittest
from pathlib import Path

from modules.logging_config import setup_logging


class LoggingConfigTests(unittest.TestCase):
    def tearDown(self):
        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            handler.close()

    def test_newest_entries_are_at_top_and_per_run_files_retained(self):
        with tempfile.TemporaryDirectory() as root_dir:
            log_dir = Path(root_dir) / "logs"
            logger = setup_logging(
                log_dir=log_dir,
                log_file=Path("shai.log"),
                per_run_file=True,
                newest_first=True,
                retain_files=10,
            )
            logger.info("older-entry")
            logger.warning("newer-entry")
            files = list(log_dir.glob("shai_*.log"))
            self.assertEqual(len(files), 1)
            content = files[0].read_text()
            self.assertLess(content.index("newer-entry"), content.index("older-entry"))
            self.assertFalse(any(isinstance(h, logging.StreamHandler) for h in logger.handlers))

    def test_retention_keeps_only_last_n_run_logs(self):
        with tempfile.TemporaryDirectory() as root_dir:
            log_dir = Path(root_dir) / "logs"
            log_dir.mkdir()
            for index in range(12):
                (log_dir / "shai_{}.log".format(index)).write_text(str(index))
            setup_logging(log_dir=log_dir, log_file=Path("shai.log"), retain_files=10)
            self.assertLessEqual(len(list(log_dir.glob("shai_*.log"))), 10)


if __name__ == "__main__":
    unittest.main()
