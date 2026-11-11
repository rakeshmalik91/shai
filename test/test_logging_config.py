import logging
import tempfile
import unittest
from pathlib import Path

from modules import logging_config
from modules.logging_config import setup_logging
from modules.project_paths import get_active_project_root, set_active_project_root


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
        original_project_root = get_active_project_root()
        with tempfile.TemporaryDirectory() as root_dir:
            try:
                set_active_project_root(root_dir)
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
                set_active_project_root(original_project_root)

    def test_legacy_logs_config_value_resolves_under_shai(self):
        original_project_root = get_active_project_root()
        with tempfile.TemporaryDirectory() as root_dir:
            try:
                set_active_project_root(root_dir)
                settings = logging_config.load_logging_config({"logging": {}})
                self.assertEqual(settings["log_dir"], Path(root_dir) / ".shai" / "logs")
            finally:
                set_active_project_root(original_project_root)

    def test_load_logging_config_caller_overrides_dedicated_asset(self):
        # Caller-supplied logging overrides take precedence over assets/logging_config.json
        settings = logging_config.load_logging_config({"logging": {"log_level": "WARNING", "retain_files": 42}})
        self.assertEqual(settings["log_level"], logging.WARNING)
        self.assertEqual(settings["retain_files"], 42)

    def test_init_default_logging_reads_logging_config_asset(self):
        settings = logging_config.load_logging_config(None)
        # Default in assets/logging_config.json is log_level=DEBUG, retain_files=10
        self.assertEqual(settings["log_level"], logging.DEBUG)
        self.assertEqual(settings["retain_files"], 10)


if __name__ == "__main__":
    unittest.main()
