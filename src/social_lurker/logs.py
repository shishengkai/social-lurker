"""Local bounded diagnostics: codes and times only, no free text or user material."""

import os
import re

from .util import canonical, lock


def record(instance, code, *, maximum=2 * 1024 * 1024):
    if not re.fullmatch("[A-Z_]{1,80}", code):
        return
    # Diagnostics must never change an already determined operation result.
    try:
        with lock(instance.root, "state.lock", timeout=0):
            directory = instance.path("logs")
            directory.mkdir(exist_ok=True, mode=0o700)
            log = instance.path("logs/lurker.log")
            if log.exists() and log.stat().st_size >= maximum:
                old = instance.path("logs/lurker.log.2")
                old.unlink(missing_ok=True)
                previous = instance.path("logs/lurker.log.1")
                if previous.exists():
                    previous.rename(old)
                log.rename(previous)
            fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "a") as stream:
                stream.write(canonical({"at": int(instance.clock()), "code": code}) + "\n")
    except Exception:
        pass
