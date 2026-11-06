"""Centralized per-run logging for Shai.

Logs are written only to files (never stdout/stderr). Each process gets its own
 timestamped log, and records are prepended so the newest entry is at the top.
"""

import datetime
import json
import logging
import json
import os
import pathlib
import shutil
import threading
from typing import Optional

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIR = SCRIPT_DIR / ".shai" / "logs"
DEFAULT_LOG_FILE = "shai.log"
DEFAULT_RETAIN_FILES = 10
DEFAULT_LOG_LEVEL = logging.DEBUG
DEFAULT_CONSOLE_LEVEL = logging.INFO
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class PrependFileHandler(logging.Handler):
    """A small single-process handler that places each new record at file top."""

    def __init__(self, filename, newest_first=True, encoding="utf-8"):
        logging.Handler.__init__(self)
        self.baseFilename = str(pathlib.Path(filename).resolve())
        self.newest_first = bool(newest_first)
        self.encoding = encoding
        self._lock = threading.RLock()
        pathlib.Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
        with open(self.baseFilename, "a", encoding=self.encoding):
            pass

    def emit(self, record):
        try:
            message = self.format(record) + "\n"
            with self._lock:
                if self.newest_first:
                    with open(self.baseFilename, "r+", encoding=self.encoding) as handle:
                        previous = handle.read()
                        handle.seek(0)
                        handle.write(message)
                        handle.write(previous)
                        handle.truncate()
                else:
                    with open(self.baseFilename, "a", encoding=self.encoding) as handle:
                        handle.write(message)
        except Exception:
            self.handleError(record)

    def close(self):
        logging.Handler.close(self)


def _timestamped_log_path(log_dir, log_file):
    log_file = pathlib.Path(log_file)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    name = "{}_{}_{}{}".format(log_file.stem, stamp, os.getpid(), log_file.suffix or ".log")
    return pathlib.Path(log_dir) / name


def _prune_run_logs(log_dir, log_file, retain_files):
    """Keep only the newest N Shai run logs matching this configured basename."""
    if retain_files < 1:
        retain_files = 1
    log_file = pathlib.Path(log_file)
    pattern = log_file.stem + "_*" + (log_file.suffix or ".log")
    candidates = list(pathlib.Path(log_dir).glob(pattern))
    candidates.sort(key=lambda path: (path.stat().st_mtime, path.name), reverse=True)
    for old_path in candidates[retain_files:]:
        try:
            old_path.unlink()
        except OSError:
            pass


def _migrate_legacy_logs(log_dir):
    """Move legacy root-level logs/ into the canonical .shai/logs/ directory."""
    log_dir = pathlib.Path(log_dir)
    legacy_dir = SCRIPT_DIR / "logs"
    canonical_dir = SCRIPT_DIR / ".shai" / "logs"
    try:
        if log_dir.resolve() != canonical_dir.resolve() or not legacy_dir.is_dir():
            return
        canonical_dir.mkdir(parents=True, exist_ok=True)
        for source in legacy_dir.iterdir():
            destination = canonical_dir / source.name
            if destination.exists():
                continue
            try:
                shutil.move(str(source), str(destination))
            except OSError:
                # Keep the original file if it cannot safely be migrated.
                continue
        try:
            legacy_dir.rmdir()
        except OSError:
            pass
    except OSError:
        # Logging must still initialize even if legacy migration is unavailable.
        pass


def setup_logging(
    log_dir: Optional[pathlib.Path] = None,
    log_file: Optional[pathlib.Path] = None,
    max_bytes=5 * 1024 * 1024,
    backup_count=5,
    log_level=DEFAULT_LOG_LEVEL,
    console_level=DEFAULT_CONSOLE_LEVEL,
    enable_console=False,
    per_run_file=True,
    newest_first=True,
    retain_files=DEFAULT_RETAIN_FILES,
):
    """Configure file-only logging, timestamped per run, newest records first."""
    if log_dir is None:
        log_dir = DEFAULT_LOG_DIR
    log_dir = pathlib.Path(log_dir)
    _migrate_legacy_logs(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    if log_file is None:
        log_file = DEFAULT_LOG_FILE
    log_file = pathlib.Path(log_file)
    if log_file.is_absolute():
        try:
            relative_name = log_file.relative_to(log_dir)
        except ValueError:
            relative_name = pathlib.Path(log_file.name)
        log_file = relative_name

    active_path = _timestamped_log_path(log_dir, log_file) if per_run_file else log_dir / log_file
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    handler = PrependFileHandler(active_path, newest_first=newest_first)
    handler.setLevel(log_level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    root_logger.addHandler(handler)
    if per_run_file:
        _prune_run_logs(log_dir, log_file, int(retain_files))
    # Deliberately no StreamHandler: the interactive terminal stays clean.
    root_logger.info("Logging initialized. Log file: %s", active_path)
    root_logger.debug(
        "Log config: per_run_file=%s newest_first=%s retain_files=%s max_bytes=%s backup_count=%s",
        per_run_file, newest_first, retain_files, max_bytes, backup_count
    )
    return root_logger


def get_logger(name):
    return logging.getLogger(name)


def load_logging_config(config):
    settings = {}
    config_path = pathlib.Path(__file__).resolve().parent.parent / "assets" / "logging_config.json"
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            dedicated = json.load(handle)
        if isinstance(dedicated, dict):
            settings.update(dedicated)
    except (OSError, ValueError, TypeError):
        pass
    if isinstance(config, dict):
        if "logging" in config and isinstance(config["logging"], dict):
            settings.update(config["logging"])
        else:
            for k in ("enabled", "log_dir", "log_file", "log_level", "max_bytes", "backup_count",
                      "console_level", "enable_console", "per_run_file", "newest_first", "retain_files"):
                if k in config:
                    settings[k] = config[k]
    if not settings.get("enabled", True):
        return {"enabled": False}
    level_map = {
        "DEBUG": logging.DEBUG, "INFO": logging.INFO,
        "WARNING": logging.WARNING, "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    configured_log_dir = pathlib.Path(settings.get("log_dir", ".shai/logs"))
    if not configured_log_dir.is_absolute():
        # Treat the historical "logs" setting as the new canonical location.
        if configured_log_dir == pathlib.Path("logs"):
            configured_log_dir = pathlib.Path(".shai") / "logs"
        log_dir = SCRIPT_DIR / configured_log_dir
    else:
        log_dir = configured_log_dir
    log_file = pathlib.Path(settings.get("log_file", DEFAULT_LOG_FILE))
    if log_file.is_absolute():
        try:
            log_file = log_file.relative_to(log_dir)
        except ValueError:
            log_file = pathlib.Path(log_file.name)
    level = str(settings.get("log_level", "DEBUG")).upper()
    return {
        "enabled": True,
        "log_dir": log_dir,
        "log_file": log_file,
        "max_bytes": settings.get("max_bytes", 5 * 1024 * 1024),
        "backup_count": settings.get("backup_count", 5),
        "log_level": level_map.get(level, DEFAULT_LOG_LEVEL),
        "console_level": level_map.get(str(settings.get("console_level", "INFO")).upper(), DEFAULT_CONSOLE_LEVEL),
        "enable_console": settings.get("enable_console", False) is True,
        "per_run_file": settings.get("per_run_file", True) is True,
        "newest_first": settings.get("newest_first", True) is True,
        "retain_files": settings.get("retain_files", DEFAULT_RETAIN_FILES),
    }


_default_logger = None


def init_default_logging(config=None):
    global _default_logger
    if _default_logger is not None:
        return _default_logger
    if config is None:
        try:
            with (SCRIPT_DIR / "assets" / "logging_config.json").open("r", encoding="utf-8") as handle:
                config = json.load(handle)
        except (OSError, ValueError, TypeError):
            try:
                with (SCRIPT_DIR / "assets" / "shai_config.json").open("r", encoding="utf-8") as handle:
                    config = json.load(handle)
            except (OSError, ValueError, TypeError):
                config = {}
    settings = load_logging_config(config)
    if not settings.get("enabled", True):
        null_logger = logging.getLogger("shai")
        null_logger.addHandler(logging.NullHandler())
        null_logger.setLevel(logging.CRITICAL + 1)
        _default_logger = null_logger
        return _default_logger
    settings.pop("enabled", None)
    _default_logger = setup_logging(**settings)
    return _default_logger


_SENSITIVE_LOG_FIELDS = {
    "authorization", "api_key", "apikey", "x_api_key", "key",
    "thinking", "reasoning", "reasoning_content", "reasoning_details", "analysis",
    "chain_of_thought", "internal_reasoning",
}


def _redact_log_value(value):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if str(key).lower().replace("-", "_") in _SENSITIVE_LOG_FIELDS
            else _redact_log_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_log_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_log_value(item) for item in value]
    return value


def log_api_payload(logger, label, payload, max_chars=50000):
    if not logger.isEnabledFor(logging.DEBUG):
        return
    try:
        rendered = json.dumps(_redact_log_value(payload), ensure_ascii=False, indent=2, sort_keys=True)
    except (TypeError, ValueError):
        rendered = repr(payload)
    if len(rendered) > max_chars:
        rendered = rendered[:max_chars] + "\n...[TRUNCATED; total serialized chars={}]".format(len(rendered))
    logger.debug("%s payload:\n%s", label, rendered)
