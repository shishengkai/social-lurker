import json
import select
import sys
import tempfile
import uuid

from . import __version__
from .errors import LurkerError


def summary():
    return dict.fromkeys(
        (
            "authors_total",
            "authors_succeeded",
            "authors_failed",
            "authors_unchecked",
            "pages_committed",
            "new_works",
            "requests",
        ),
        0,
    )


class Output:
    def __init__(self, command=None, profile_id=None, fmt="jsonl", stream=None):
        self.stream = stream if stream is not None else sys.stdout
        self.fmt = fmt
        self.common = {
            "schema_version": 1,
            "cli_version": __version__,
            "run_id": str(uuid.uuid4()),
            "command": command,
            "profile_id": profile_id,
        }
        self.spool = tempfile.TemporaryFile(mode="w+t", encoding="utf-8") if fmt == "json" else None
        self.finished = False

    def ensure_open(self):
        # Check before each request even when no work has been emitted yet.
        if getattr(self.stream, "closed", False):
            raise LurkerError("OUTPUT_CLOSED")
        try:
            fd = self.stream.fileno()
        except (AttributeError, OSError, ValueError):
            return
        poll = select.poll()
        poll.register(fd, select.POLLERR | select.POLLHUP | select.POLLNVAL)
        if poll.poll(0):
            raise LurkerError("OUTPUT_CLOSED")

    def write(self, value):
        try:
            self.stream.write(value)
            self.stream.flush()
        except (BrokenPipeError, OSError, ValueError):
            raise LurkerError("OUTPUT_CLOSED") from None

    def record(self, kind, **fields):
        self.ensure_open()
        item = {**self.common, "type": kind, **fields}
        line = json.dumps(item, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
        if self.spool:
            try:
                self.spool.write(line)
            except OSError:
                raise LurkerError("OUTPUT_CLOSED") from None
        else:
            self.write(line)
        return item

    def complete(self, status, stats, scan_complete=None):
        if self.finished:
            raise RuntimeError("completion already written")
        self.finished = True
        completion = {
            **self.common,
            "type": "complete",
            "status": status,
            "scan_complete": scan_complete,
            "summary": stats,
        }
        if self.spool:
            self.spool.seek(0)
            prefix = json.dumps(self.common, ensure_ascii=False)[:-1] + ',"records":['
            self.write(prefix)
            first = True
            for line in self.spool:
                self.write(("" if first else ",") + line.rstrip("\n"))
                first = False
            self.write('],"completion":' + json.dumps(completion, ensure_ascii=False) + "}\n")
        else:
            self.write(json.dumps(completion, ensure_ascii=False, separators=(",", ":")) + "\n")

    def close(self):
        if self.spool:
            self.spool.close()
