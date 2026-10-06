"""Fixed TikHub origin, serial pacing, bounded retries, sanitized typed errors."""

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

from .config import Config
from .errors import LurkerError, require

API_ROOT = "https://api.tikhub.io/api/v1"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
USER_INFO = "/tikhub/user/get_user_info"
ENDPOINT_INFO = "/tikhub/user/get_endpoint_info"
DY = "/douyin/app/v3/"
WX = "/wechat_channels/v2/"
ENDPOINTS = {
    DY + name: "GET"
    for name in (
        "fetch_one_video_by_share_url",
        "handler_user_profile",
        "fetch_one_video",
        "fetch_user_post_videos",
    )
}
ENDPOINTS.update(
    {
        WX + name: "POST"
        for name in ("fetch_video_detail", "fetch_user_profile", "fetch_user_videos", "fetch_video_share_url")
    }
)

ENDPOINTS.update({USER_INFO: "GET", ENDPOINT_INFO: "GET"})


@dataclass(frozen=True)
class Response:
    status: int
    body: object
    headers: dict | None = None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_http(method, url, headers, body, timeout):
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener(NoRedirect())
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read(8 * 1024 * 1024 + 1)
        require(len(raw) <= 8 * 1024 * 1024)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            value = None
        return Response(response.code, value, dict(response.headers))


def send_redirect(method, url, headers, body, timeout):
    """Read only response headers: no page/media download or automatic redirects."""
    request = urllib.request.Request(url, headers=headers, method=method)
    try:
        response = urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return Response(response.code, None, dict(response.headers))


def retry_after(headers):
    value = next((v for k, v in (headers or {}).items() if k.lower() == "retry-after"), None)
    if not isinstance(value, str):
        return 1.0
    try:
        if value.isdigit():
            return float(value)
        return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return 1.0


class Transport:
    def __init__(
        self,
        secrets,
        stats,
        config=Config(),
        *,
        send=send_http,
        share_send=send_redirect,
        sleep=time.sleep,
        monotonic=time.monotonic,
        before_request=lambda: None,
    ):
        config.validate()
        self.secrets, self.stats, self.config = secrets, stats, config
        self.send, self.sleep, self.monotonic = send, sleep, monotonic
        self.share_send = share_send
        self.before_request = before_request
        self.minimum_interval = 0.0
        self.last_started = None
        self.rate_waited = 0.0
        self.rate_retry_used = False
        self.stopped = None

    def pace(self):
        self.before_request()
        if self.last_started is not None:
            delay = max(self.minimum_interval, 1.0 / self.config.rps) - (self.monotonic() - self.last_started)
            if delay > 0:
                self.sleep(delay)
        self.before_request()
        self.last_started = self.monotonic()

    def redirect(self, url):
        # Defense in depth for direct callers as well as the link resolver.
        from urllib.parse import urlsplit

        from .adapters.links import source_link

        platform, validated = source_link(url)
        require(
            platform == "douyin" and urlsplit(validated).hostname == "v.douyin.com", "SOURCE_LINK_INVALID"
        )
        if self.stopped:
            raise LurkerError(self.stopped)
        self.pace()
        try:
            result = self.share_send(
                "GET",
                validated,
                {"User-Agent": BROWSER_USER_AGENT, "Accept": "text/html,*/*;q=0.8"},
                None,
                self.config.timeout,
            )
        except (TimeoutError, socket.timeout):
            raise LurkerError("REQUEST_TIMEOUT") from None
        except urllib.error.URLError as cause:
            raise LurkerError(
                "REQUEST_TIMEOUT" if isinstance(cause.reason, TimeoutError) else "NETWORK_ERROR"
            ) from None
        except OSError:
            raise LurkerError("NETWORK_ERROR") from None
        if result.status in {403, 429}:
            self.stopped = "HTTP_BLOCKED" if result.status == 403 else "RATE_LIMITED"
            raise LurkerError(self.stopped)
        if result.status >= 500:
            raise LurkerError("NETWORK_ERROR")
        require(result.status in {301, 302, 303, 307, 308}, "SOURCE_LINK_INVALID")
        return next((v for k, v in (result.headers or {}).items() if k.lower() == "location"), None)

    def check_credentials(self):
        """Two bounded requests; authenticate only after confirming zero pricing."""
        self.minimum_interval = 1.0  # Account endpoint limit: at most one request per second.
        self.secrets.get_key()  # Invalid local configuration must not contact the server.
        pricing = self._request(
            ENDPOINT_INFO, {"endpoint": "/api/v1" + USER_INFO}, authenticate=False, retry=False
        ).get("data")
        require(
            isinstance(pricing, dict)
            and pricing.get("endpoint_uri") == "/api/v1" + USER_INFO
            and type(pricing.get("endpoint_cost")) in (int, float)
            and pricing["endpoint_cost"] == 0,
            "ENDPOINT_NOT_FREE",
        )
        account = self._request(USER_INFO, {}, retry=False)
        key_data, user_data = account.get("api_key_data"), account.get("user_data")
        require(isinstance(key_data, dict) and isinstance(user_data, dict))
        require(
            type(key_data.get("api_key_status")) is int
            and type(user_data.get("is_active")) is bool
            and type(user_data.get("account_disabled")) is bool
            and type(user_data.get("email_verified")) is bool
        )
        require(
            key_data["api_key_status"] == 1
            and user_data["is_active"]
            and not user_data["account_disabled"]
            and user_data["email_verified"],
            "AUTH_FAILED",
        )
        return {"credential_check": "passed", "endpoint_cost": 0, "business_api_tested": False}

    def call(self, endpoint, params):
        result = self._request(endpoint, params)
        data = result.get("data")
        require(isinstance(data, dict))
        if "status_code" in data:
            require(type(data["status_code"]) is int and data["status_code"] == 0)
        return data

    def _request(self, endpoint, params, *, authenticate=True, retry=True):
        require(endpoint in ENDPOINTS and isinstance(params, dict), "INPUT_INVALID")
        if self.stopped:
            raise LurkerError(self.stopped)
        require(authenticate or endpoint == ENDPOINT_INFO, "INPUT_INVALID")
        key = self.secrets.get_key() if authenticate else None
        attempts = 0
        while True:
            self.pace()
            method = ENDPOINTS[endpoint]
            url, body = API_ROOT + endpoint, None
            if method == "GET":
                url += "?" + urlencode(params)
            else:
                body = json.dumps(params, ensure_ascii=False).encode()
            self.stats["requests"] += 1
            try:
                result = self.send(
                    method,
                    url,
                    {
                        **({"Authorization": "Bearer " + key} if key is not None else {}),
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "User-Agent": BROWSER_USER_AGENT,
                    },
                    body,
                    self.config.timeout,
                )
            except (TimeoutError, socket.timeout):
                error = LurkerError("REQUEST_TIMEOUT")
            except urllib.error.URLError as cause:
                error = LurkerError(
                    "REQUEST_TIMEOUT" if isinstance(cause.reason, TimeoutError) else "NETWORK_ERROR"
                )
            except OSError:
                error = LurkerError("NETWORK_ERROR")
            else:
                status = result.status
                code = result.body.get("code") if isinstance(result.body, dict) else None
                effective = code if status == 200 and type(code) is int and code != 200 else status
                if status == 403 and (type(code) is not int or code != 403):
                    error = LurkerError("HTTP_BLOCKED")
                elif effective in (401, 403):
                    error = LurkerError("AUTH_FAILED")
                elif effective == 402:
                    error = LurkerError("QUOTA_UNAVAILABLE")
                elif effective == 429:
                    delay = retry_after(result.headers)
                    if retry and not self.rate_retry_used and 0 <= delay <= 30 - self.rate_waited:
                        self.rate_retry_used = True
                        self.rate_waited += delay
                        self.sleep(delay)
                        continue
                    error = LurkerError("RATE_LIMITED")
                elif status >= 500:
                    error = LurkerError("NETWORK_ERROR")
                else:
                    require(status == 200 and type(code) is int and code == 200)
                    return result.body
            if error.global_stop:
                self.stopped = error.code
                raise error
            if not retry or attempts >= self.config.retries:
                raise error
            attempts += 1
            self.sleep(min(attempts, 2))
