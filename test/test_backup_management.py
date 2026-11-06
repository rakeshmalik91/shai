import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import agent


class BackupManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = pathlib.Path(self.temp_dir.name)
        self.original_root = agent.ROOT
        agent.ROOT = self.project_root

    def tearDown(self):
        agent.ROOT = self.original_root
        self.temp_dir.cleanup()

    def test_backup_config_is_project_local(self):
        config_dir = self.project_root / ".shai"
        config_dir.mkdir()
        (config_dir / "backup_config.json").write_text(
            json.dumps({"enabled": False, "retention_days": 7}),
            encoding="utf-8",
        )
        self.assertEqual(
            agent.load_backup_config(),
            {"enabled": False, "retention_days": 7},
        )

    def test_missing_or_invalid_backup_config_uses_defaults(self):
        self.assertEqual(
            agent.load_backup_config(),
            {"enabled": True, "retention_days": 1},
        )
        config_dir = self.project_root / ".shai"
        config_dir.mkdir()
        (config_dir / "backup_config.json").write_text('{"enabled": "yes"}')
        self.assertEqual(
            agent.load_backup_config(),
            {"enabled": True, "retention_days": 1},
        )

    def test_backup_directory_is_nested_under_shai(self):
        backup_dir = agent._project_backup_dir()
        self.assertEqual(backup_dir, self.project_root / ".shai" / "shai_backups")
        self.assertTrue(backup_dir.is_dir())

    def test_legacy_backup_directory_is_migrated(self):
        legacy = self.project_root / ".shai_backups"
        legacy.mkdir()
        (legacy / "file.py.20261106-010101.bak").write_text("old")
        new_dir = agent._project_backup_dir()
        self.assertTrue((new_dir / "file.py.20261106-010101.bak").exists())
        self.assertFalse(legacy.exists())


    def test_atomic_file_edit_creates_backup_under_shai(self):
        target = self.project_root / "sample.py"
        target.write_text("value = 1\n", encoding="utf-8")
        backup = agent._atomic_replace_file(target, "value = 2\n")
        self.assertIsNotNone(backup)
        self.assertEqual(
            pathlib.Path(backup).parent,
            self.project_root / ".shai" / "shai_backups",
        )
        self.assertEqual(pathlib.Path(backup).read_text(encoding="utf-8"), "value = 1\n")

    def test_disabled_cleanup_still_migrates_legacy_directory(self):
        config_dir = self.project_root / ".shai"
        config_dir.mkdir()
        (config_dir / "backup_config.json").write_text(
            json.dumps({"enabled": False, "retention_days": 1})
        )
        legacy = self.project_root / ".shai_backups"
        legacy.mkdir()
        (legacy / "keep.bak").write_text("keep")
        agent.cleanup_old_backups()
        self.assertTrue((config_dir / "shai_backups" / "keep.bak").exists())
        self.assertFalse(legacy.exists())

    def test_cleanup_uses_project_local_retention_policy(self):
        config_dir = self.project_root / ".shai"
        config_dir.mkdir()
        (config_dir / "backup_config.json").write_text(
            json.dumps({"enabled": True, "retention_days": 0})
        )
        backup_dir = config_dir / "shai_backups"
        backup_dir.mkdir()
        old_file = backup_dir / "old.bak"
        old_file.write_text("old")
        os.utime(old_file, (time.time() - 5, time.time() - 5))
        agent.cleanup_old_backups()
        self.assertFalse(old_file.exists())


if __name__ == "__main__":
    unittest.main()
