"""Centralized logging configuration with secret redaction."""

import logging
import re
import sys
from typing import Iterable


class SecretMaskingFilter(logging.Filter):
    """Masks configured secrets and common bearer/key patterns in log messages."""

    _PATTERN_RE = re.compile(
        r"(?i)(authorization\s*[:=]\s*bearer\s+|api[_-]?key\s*[:=]\s*|token\s*[:=]\s*)([^\s,;]+)"
    )

    def __init__(self, secrets: Iterable[str] | None = None) -> None:
        super().__init__()
        self._secrets = [s for s in (secrets or []) if s]

    def update_secrets(self, secrets: Iterable[str]) -> None:
        self._secrets = [s for s in secrets if s]

    def _redact(self, text: str) -> str:
        masked = self._PATTERN_RE.sub(r"\1***REDACTED***", text)
        for secret in self._secrets:
            if secret in masked:
                masked = masked.replace(secret, "***REDACTED***")
        return masked

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self._redact(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self._redact(arg) if isinstance(arg, str) else arg
                    for arg in record.args
                )
        return True


_secret_filter = SecretMaskingFilter()


def setup_logging(level: str = "INFO", secrets: Iterable[str] | None = None) -> None:
    """Configure root and application loggers."""
    if secrets is not None:
        _secret_filter.update_secrets(secrets)

    log_level = getattr(logging, level.upper(), logging.INFO)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(_secret_filter)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)

    # Keep noisy third-party loggers reasonable while retaining uvicorn errors
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger."""
    return logging.getLogger(name)
