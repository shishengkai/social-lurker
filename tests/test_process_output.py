import json
import selectors
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from social_lurker.errors import LurkerError
from social_lurker.output import Output, summary
from social_lurker.profiles import FileRegistry

DRIVER = Path(__file__).with_name("subprocess_driver.py")


def child(tmp_path, mode):
    return subprocess.Popen(
        [sys.executable, "-B", str(DRIVER), str(tmp_path), mode],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def wait_file(path):
    deadline = time.monotonic() + 4
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists()


def first_work(proc):
    selector = selectors.DefaultSelector()
    try:
        selector.register(proc.stdout, selectors.EVENT_READ)
        assert selector.select(timeout=3), "work must be flushed before child completion"
        line = proc.stdout.readline()
        assert json.loads(line)["type"] == "work" and proc.poll() is None
        return json.loads(line)
    finally:
        selector.close()


@pytest.mark.parametrize("sig,code", [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_real_signals_stop_after_committed_page_and_complete(tmp_path, sig, code):
    proc = child(tmp_path, "signal")
    try:
        first_work(proc)
        wait_file(tmp_path / "waiting")
        proc.send_signal(sig)
        out, err = proc.communicate(timeout=3)
        assert proc.returncode == code and err == ""
        records = [json.loads(line) for line in out.splitlines()]
        assert [r["type"] for r in records] == ["error", "complete"]
        assert records[0]["error"]["code"] == "INTERRUPTED"
        assert records[-1]["summary"]["authors_failed"] == 1
        assert records[-1]["scan_complete"] is False and records[-1]["status"] == "error"
        assert len((tmp_path / "calls").read_text().splitlines()) == 2
        with sqlite3.connect(FileRegistry(tmp_path / "data").get("p0001").db_path) as db:
            assert db.execute("SELECT work_id FROM works").fetchall() == [("new",)]
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_pipe_close_stops_after_inflight_and_does_not_request_third_page(tmp_path):
    proc = child(tmp_path, "pipe")
    try:
        first_work(proc)
        wait_file(tmp_path / "waiting")
        proc.stdout.close()
        (tmp_path / "reader_closed").touch()
        assert proc.wait(timeout=3) == 141
        assert proc.stderr.read() == ""
        assert len((tmp_path / "calls").read_text().splitlines()) == 2
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_json_spool_keeps_records_off_heap_and_preserves_order(tmp_path):
    import io

    out = io.StringIO()
    writer = Output("check", "p0001", "json", out)
    try:
        for i in range(5000):
            writer.record("work", work={"work_id": str(i)})
        assert out.getvalue() == "" and writer.spool.tell() > 500000
        writer.complete("ok", summary(), True)
        data = json.loads(out.getvalue())
        assert [r["work"]["work_id"] for r in data["records"]] == list(map(str, range(5000)))
        assert data["completion"]["type"] == "complete"
        with pytest.raises(RuntimeError):
            writer.complete("ok", summary())
    finally:
        writer.close()


def test_closed_writer_stops_immediately(env):
    *_, output, adapter, service = env
    output.stream.close()
    with pytest.raises(LurkerError) as error:
        output.record("result", payload={})
    assert error.value.code == "OUTPUT_CLOSED" and adapter.requests == []
