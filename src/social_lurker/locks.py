"""Non-waiting OS locks (macOS/Linux); existence of a file never means busy."""

import fcntl
from contextlib import contextmanager
from pathlib import Path

from .errors import LurkerError


@contextmanager
def lock(path, *, shared=False, code="PROFILE_BUSY"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+b") as f:
        try:
            fcntl.flock(f, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LurkerError(code) from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
