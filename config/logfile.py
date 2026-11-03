"""The log files the Log Viewer (Tools -> Logs) reads.

The API writes APP_DATA_DIR/logs/app.log and the scheduler APP_DATA_DIR/logs/scheduler.log.
They run in separate containers, so docker-compose.yml mounts the same host folder (LOG_DIR)
at /app/logs in both; without it the API cannot see the scheduler's file.
"""
import logging
import os
from logging.handlers import RotatingFileHandler

LOG_MAX_BYTES = 5_000_000
LOG_BACKUPS = 3


def log_dir() -> str:
    return os.path.join(os.getenv("APP_DATA_DIR", "."), "logs")


def file_handler(name: str, tag: str) -> logging.Handler:
    """A size-capped handler for log_dir()/name. Every line starts with the same timestamp
    format, so the Log Viewer can merge the two files in time order."""
    os.makedirs(log_dir(), exist_ok=True)
    handler = RotatingFileHandler(
        os.path.join(log_dir(), name), maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(f"%(asctime)s %(levelname)s [{tag}] %(message)s"))
    return handler
