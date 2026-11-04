"""Centralized logging configuration for Shai."""

import logging
import json
import logging.handlers
import pathlib
from typing import Optional


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIR = SCRIPT_DIR / "logs"
DEFAULT_LOG_FILE = DEFAULT_LOG_DIR / "shai.log"
DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
DEFAULT_BACKUP_COUNT = 5
DEFAULT_LOG_LEVEL = logging.DEBUG
DEFAULT_CONSOLE_LEVEL = logging.INFO

# Log format with timestamp
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    log_dir: Optional[pathlib.Path] = None,
    log_file: Optional[pathlib.Path] = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    log_level: int = DEFAULT_LOG_LEVEL,
    console_level: int = DEFAULT_CONSOLE_LEVEL,
    enable_console: bool = True,
) -> logging.Logger:
    """
    Configure logging with file rotation and optional console output.

    Args:
        log_dir: Directory for log files (default: ./logs)
        log_file: Specific log file path (default: ./logs/shai.log)
        max_bytes: Max size per log file before rotation
        backup_count: Number of rotated log files to keep
        log_level: Logging level for file handler
        console_level: Logging level for console handler
        enable_console: Deprecated compatibility option; console logging is always disabled

    Returns:
        The root logger configured with handlers
    """
    if log_dir is None:
        log_dir = DEFAULT_LOG_DIR
    if log_file is None:
        log_file = DEFAULT_LOG_FILE

    # Ensure log directory exists
    log_dir.mkdir(parents=True, exist_ok=True)

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Capture all levels, handlers filter

    # Clear existing handlers to avoid duplicates on reconfiguration
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # File handler with rotation
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # Shai is an interactive terminal application. Keep ALL diagnostics in
    # the rotating file log; never attach a stream handler to stdout or stderr.
    # enable_console/console_level remain accepted for config compatibility.

    # Log startup message
    root_logger.info("Logging initialized. Log file: %s", log_file)
    root_logger.debug(
        "Log config: max_bytes=%d, backup_count=%d, log_level=%s, console_level=%s",
        max_bytes,
        backup_count,
        logging.getLevelName(log_level),
        logging.getLevelName(console_level),
    )

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for a module."""
    return logging.getLogger(name)


def load_logging_config(config: dict) -> dict:
    """
    Load logging configuration from shai_config.json.

    Expected config structure:
    {
        "logging": {
            "enabled": true,
            "log_dir": "logs",
            "log_file": "shai.log",
            "max_bytes": 5242880,
            "backup_count": 5,
            "log_level": "DEBUG",
            "console_level": "INFO",
            "enable_console": true
        }
    }
    """
    logging_config = config.get("logging", {})
    if not logging_config.get("enabled", True):
        return {"enabled": False}

    # Parse log level strings
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }

    log_level_str = logging_config.get("log_level", "DEBUG").upper()
    console_level_str = logging_config.get("console_level", "INFO").upper()

    log_dir = pathlib.Path(logging_config.get("log_dir", "logs"))
    if not log_dir.is_absolute():
        log_dir = SCRIPT_DIR / log_dir
    log_file = pathlib.Path(logging_config.get("log_file", "shai.log"))
    if not log_file.is_absolute():
        log_file = log_dir / log_file

    return {
        "enabled": True,
        "log_dir": log_dir,
        "log_file": log_file,
        "max_bytes": logging_config.get("max_bytes", DEFAULT_MAX_BYTES),
        "backup_count": logging_config.get("backup_count", DEFAULT_BACKUP_COUNT),
        "log_level": level_map.get(log_level_str, DEFAULT_LOG_LEVEL),
        "console_level": level_map.get(console_level_str, DEFAULT_CONSOLE_LEVEL),
        "enable_console": False,
    }


# Initialize default logging on import (can be reconfigured later)
_default_logger = None


def init_default_logging(config: Optional[dict] = None) -> logging.Logger:
    """Initialize logging with config from shai_config.json or defaults."""
    global _default_logger
    if _default_logger is not None:
        return _default_logger

    if config is None:
        # Try to load from config file
        try:
            import json
            config_file = SCRIPT_DIR / "shai_config.json"
            if config_file.exists():
                with config_file.open("r", encoding="utf-8") as f:
                    config = json.load(f)
        except (OSError, json.JSONDecodeError):
            config = {}

    logging_config = load_logging_config(config)
    if not logging_config.get("enabled", True):
        # Create a null logger that does nothing
        null_logger = logging.getLogger("shai")
        null_logger.addHandler(logging.NullHandler())
        null_logger.setLevel(logging.CRITICAL + 1)
        _default_logger = null_logger
        return _default_logger

    logging_config.pop("enabled", None)
    _default_logger = setup_logging(**logging_config)
    return _default_logger

# Fields that should never be written to diagnostic payload logs.
_SENSITIVE_LOG_FIELDS = {
    "authorization", "api_key", "apikey", "x_api_key", "key",
    "thinking", "reasoning", "reasoning_content", "reasoning_details", "analysis",
    "chain_of_thought", "internal_reasoning",
}


def _redact_log_value(value):
    """Return a JSON-safe copy with credentials and private reasoning removed."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _SENSITIVE_LOG_FIELDS:
                result[key] = "[REDACTED]"
            else:
                result[key] = _redact_log_value(item)
        return result
    if isinstance(value, list):
        return [_redact_log_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_log_value(item) for item in value]
    return value


def log_api_payload(logger, label, payload, max_chars=50000):
    """Log bounded API JSON payloads at DEBUG, with sensitive fields redacted."""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    try:
        rendered = json.dumps(
            _redact_log_value(payload), ensure_ascii=False, indent=2, sort_keys=True
        )
    except (TypeError, ValueError):
        rendered = repr(payload)

    if len(rendered) > max_chars:
        rendered = rendered[:max_chars] + (
            "\\n...[TRUNCATED; total serialized chars={}]".format(len(rendered))
        )
    logger.debug("%s payload:\\n%s", label, rendered)
