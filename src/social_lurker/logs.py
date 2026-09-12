import logging
from logging.handlers import RotatingFileHandler


def record(directory, command, status, settings=None):
    """Only classifications enter logs. Never log raw exceptions, requests or bodies."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            directory / "events.log",
            maxBytes=(settings or {}).get("max_file_mb", 10) * 1024 * 1024,
            backupCount=(settings or {}).get("backup_count", 5),
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        event = logging.LogRecord(
            "social_lurker", logging.INFO, "", 0, "command=%s status=%s", (command, status), None
        )
        handler.emit(event)
        handler.close()
    except OSError:
        pass  # A log failure cannot roll back committed business results or disclose request data.
