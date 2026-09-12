"""Single-instance serialized TikHub requests with persistent rate and fault gates."""

import email.utils
import http.client
import random
import ssl
import time
from datetime import UTC
from urllib.parse import urlencode

from .config import automatic_gate
from .errors import LurkerError, require
from .schedule import allowed, utc_day
from .sources import ENDPOINTS, validate_params, validate_probe
from .state import add_hold, applicable, holds
from .store import active
from .util import canonical, lock, parse_json


class Response:
    def __init__(self, status, data, headers=None):
        self.status, self.data, self.headers = status, data, headers or {}


def transport(endpoint, params, key, timeout):
    """No redirects, no SDK retry; 5s connect and a 35s wall deadline, bounded JSON."""
    started = time.monotonic()
    conn = http.client.HTTPSConnection(
        "api.tikhub.io", timeout=min(5, timeout), context=ssl.create_default_context()
    )
    method = ENDPOINTS[endpoint]
    path = "/api/v1" + endpoint
    data = canonical(params).encode() if method == "POST" else None
    if method == "GET":
        path += "?" + urlencode(params)
    try:
        conn.connect()
        conn.sock.settimeout(max(0.01, min(30, timeout - (time.monotonic() - started))))
        conn.request(
            method,
            path,
            body=data,
            headers={
                "Authorization": "Bearer " + key,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Content-Type": "application/json",
            },
        )
        response = conn.getresponse()
        content = bytearray()
        while True:
            left = timeout - (time.monotonic() - started)
            require(left > 0, "HTTP_TIMEOUT")
            # read1 performs at most one buffered socket read per iteration.
            sock = conn.sock or getattr(getattr(response.fp, "raw", None), "_sock", None)
            if sock:
                sock.settimeout(max(0.01, min(30, left)))
            part = response.read1(65536)
            if not part:
                break
            content.extend(part)
            require(len(content) <= 5 * 1024 * 1024, "RESPONSE_TOO_LARGE")
        try:
            body = parse_json(content, 5 * 1024 * 1024)
        except LurkerError:
            body = None
        return Response(
            response.status,
            body,
            {k.lower(): v for k, v in response.getheaders() if k.lower() == "retry-after"},
        )
    except (OSError, http.client.HTTPException):
        raise LurkerError("HTTP_TEMPORARY", "数据请求暂时失败", retryable=True) from None
    finally:
        conn.close()


def retry_after(headers, now):
    raw = headers.get("retry-after")
    if not isinstance(raw, str):
        return now + 30
    try:
        return max(now + 30, now + max(0, int(raw)))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return max(now + 30, int(parsed.timestamp()))
        except (ValueError, TypeError, OverflowError):
            return now + 30


class Client:
    def __init__(self, instance, *, send=transport, sleep=time.sleep, monotonic=time.monotonic):
        self.instance, self.send, self.sleep, self.monotonic = instance, send, sleep, monotonic

    def call(
        self,
        endpoint,
        params,
        *,
        automatic=False,
        deadline=None,
        watch=None,
        verify_hold=None,
        failure_count=0,
    ):
        validate_params(endpoint, params)
        deadline = deadline if deadline is not None else self.monotonic() + 90
        key = self.instance.credentials()
        with lock(self.instance.root, "request.lock", timeout=max(0, min(5, deadline - self.monotonic()))):
            exempt = set()
            while True:
                now = self.instance.clock()
                with self.instance.transaction("permit") as (db, settings):
                    if automatic:
                        require(allowed(now, settings), "QUIET_HOURS")
                        automatic_gate(settings)
                    if watch:
                        current_watch = active(db, *watch)
                        failure_count = max(failure_count, current_watch["failure_count"])
                    current = holds(db)
                    if verify_hold:
                        chosen = next((h for h in current if h["hold_id"] == verify_hold), None)
                        require(chosen is not None and chosen["endpoint"] == endpoint, "HOLD_TARGET_INVALID")
                        exempt = {h["hold_id"] for h in current if applicable(h, endpoint)}
                    require(
                        not any(applicable(h, endpoint) and h["hold_id"] not in exempt for h in current),
                        "API_HELD",
                        "接口故障待修复，请验证并恢复",
                    )
                    rt = dict(db.execute("SELECT * FROM runtime").fetchone())
                    require(
                        rt["api_blocked_until"] is None or now >= rt["api_blocked_until"],
                        "API_RATE_WAIT",
                        "限流退避尚未到期",
                    )
                    rate = settings["rate_limit"]
                    rps = rate["endpoint_overrides"].get(endpoint, rate["requests_per_second"])
                    interval = 1000 / rps
                    wait = max(0, ((rt["api_next_start_at_ms"] or 0) - now * 1000) / 1000)
                    require(deadline - self.monotonic() >= wait + 35, "TIME_SLICE_END")
                    if wait <= 0:
                        day = utc_day(now)
                        count = rt["requests_reserved"] if rt["request_count_day"] == day else 0
                        db.execute(
                            "UPDATE runtime SET request_count_day=?,requests_reserved=?,api_next_start_at_ms=?,updated_at=?",
                            (day, count + 1, int(now * 1000 + interval), int(now)),
                        )
                if wait <= 0:
                    break
                self.sleep(wait)
            response = None
            failure = None
            try:
                response = self.send(endpoint, params, key, min(35, deadline - self.monotonic()))
            except LurkerError as exc:
                failure = exc
            except (OSError, ValueError, TypeError):
                failure = LurkerError("HTTP_TEMPORARY", retryable=True)
            finally:
                now = self.instance.clock()
                with self.instance.transaction("result") as (db, _):
                    db.execute(
                        "UPDATE runtime SET api_next_start_at_ms=max(coalesce(api_next_start_at_ms,0),?),updated_at=?",
                        (int(now * 1000 + interval), int(now)),
                    )
                    if response is not None:
                        status = response.status
                        if status == 200 and isinstance(response.data, dict):
                            status = response.data.get("code")
                        if status in (401, 402, 403):
                            code = {401: "API_AUTH", 402: "API_QUOTA", 403: "API_PERMISSION"}[status]
                            add_hold(db, endpoint, code, int(now))
                            failure = LurkerError(code, "数据接口需要修复后验证")
                        elif status == 429:
                            backoff = (30, 120, 600)[min(failure_count, 2)]
                            until = max(
                                retry_after(response.headers, now),
                                now + backoff + random.uniform(0, backoff * 0.1),
                            )
                            db.execute(
                                "UPDATE runtime SET api_blocked_until=max(coalesce(api_blocked_until,0),?)",
                                (int(until),),
                            )
                            failure = LurkerError(
                                "API_RATE_WAIT", "服务商限流，等待后续允许轮次", retryable=True
                            )
                        elif response.status != 200 or status != 200:
                            failure = LurkerError("UPSTREAM_TEMPORARY", "接口未返回有效数据", retryable=True)
                        else:
                            body = response.data.get("data")
                            if not isinstance(body, dict) or body.get("status_code", 0) != 0:
                                failure = LurkerError("RESPONSE_INVALID")
                            elif verify_hold:
                                try:
                                    validate_probe(endpoint, body, params)
                                except LurkerError as exc:
                                    failure = exc
                                # Same-path success proves these exact incident contracts, not another path's entitlement.
                                retained = [
                                    h
                                    for h in holds(db)
                                    if failure or not (h["hold_id"] in exempt and h["endpoint"] == endpoint)
                                ]
                                db.execute("UPDATE runtime SET api_holds_json=?", (canonical(retained),))
            if failure:
                failure.request_attempted = True
                if failure.retryable and failure.code != "API_RATE_WAIT":
                    backoff = (30, 120, 600)[min(failure_count, 2)]
                    until = self.instance.clock() + backoff + random.uniform(0, backoff * 0.1)
                    with self.instance.transaction("result") as (db, _):
                        db.execute(
                            "UPDATE runtime SET api_next_start_at_ms=max(coalesce(api_next_start_at_ms,0),?)",
                            (int(until * 1000),),
                        )
                raise failure
            return response.data["data"]

    def verify(self, hold_id, params):
        with self.instance.transaction("read") as (db, _):
            h = next((h for h in holds(db) if h["hold_id"] == hold_id), None)
            require(h is not None, "HOLD_NOT_FOUND")
            endpoint = h["endpoint"]
        self.call(endpoint, params, verify_hold=hold_id)
        return {"verified_endpoint": endpoint, "notification_sent": False}
