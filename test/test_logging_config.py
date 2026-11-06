import logging
import tempfile
import unittest
from pathlib import Path

from modules import logging_config
from modules.logging_config import setup_logging


class LoggingConfigTests(unittest.TestCase):
    def tearDown(self):
        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)
            handler.close()

    def test_newest_entries_are_at_top_and_per_run_files_retained(self):
        with tempfile.TemporaryDirectory() as root_dir:
            log_dir = Path(root_dir) / ".shai" / "logs"
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
            log_dir = Path(root_dir) / ".shai" / "logs"
            log_dir.mkdir(parents=True)
            for index in range(12):
                (log_dir / "shai_{}.log".format(index)).write_text(str(index))
            setup_logging(log_dir=log_dir, log_file=Path("shai.log"), retain_files=10)
            self.assertLessEqual(len(list(log_dir.glob("shai_*.log"))), 10)


    def test_default_log_location_is_under_shai_and_migrates_legacy_logs(self):
        original_script_dir = logging_config.SCRIPT_DIR
        with tempfile.TemporaryDirectory() as root_dir:
            original_default_log_dir = logging_config.DEFAULT_LOG_DIR
            try:
                logging_config.SCRIPT_DIR = Path(root_dir)
                logging_config.DEFAULT_LOG_DIR = Path(root_dir) / ".shai" / "logs"
                legacy = Path(root_dir) / "logs"
                legacy.mkdir()
                (legacy / "old.log").write_text("legacy")
                logger = setup_logging(per_run_file=False, log_file=Path("shai.log"))
                expected = Path(root_dir) / ".shai" / "logs"
                self.assertTrue(expected.is_dir())
                self.assertEqual((expected / "old.log").read_text(), "legacy")
                self.assertFalse(legacy.exists())
                self.assertTrue(any(str(expected) in h.baseFilename for h in logger.handlers
                                    if hasattr(h, "baseFilename")))
            finally:
                logging_config.SCRIPT_DIR = original_script_dir
                logging_config.DEFAULT_LOG_DIR = original_default_log_dir

    def test_legacy_logs_config_value_resolves_under_shai(self):
        original_script_dir = logging_config.SCRIPT_DIR
        with tempfile.TemporaryDirectory() as root_dir:
            try:
                logging_config.SCRIPT_DIR = Path(root_dir)
                settings = logging_config.load_logging_config({"logging": {}})
                self.assertEqual(settings["log_dir"], Path(root_dir) / ".shai" / "logs")
            finally:
                logging_config.SCRIPT_DIR = original_script_dir


if __name__ == "__main__":
    unittest.main()
