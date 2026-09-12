from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import httpx

from ..errors import LurkerError, require


def client():
    return httpx.Client(timeout=httpx.Timeout(60, connect=15), follow_redirects=False, trust_env=False)


def check_response(response):
    status = response.status_code
    if status in (401, 403):
        raise LurkerError("AUTH_REQUIRED", "凭据无效、权限不足或任务归属不同")
    if status == 402:
        raise LurkerError("QUOTA_EXHAUSTED", "服务额度不足")
    if status == 429:
        retry = response.headers.get("Retry-After", "60")
        try:
            seconds = max(0, float(retry))
        except ValueError:
            try:
                seconds = max(0, (parsedate_to_datetime(retry) - datetime.now(UTC)).total_seconds())
            except (ValueError, TypeError):
                seconds = 60
        raise LurkerError("RATE_LIMITED", "服务限流，稍后继续", retryable=True, retry_after=seconds)
    if status in (404, 410):
        raise LurkerError("UNAVAILABLE", "请求的资料或远端任务不可访问，需核对")
    if status >= 500:
        raise LurkerError("UPSTREAM_TEMPORARY", "服务暂时不可用", retryable=True)
    require(200 <= status < 300, "UPSTREAM_REJECTED", "服务拒绝请求")


def request(http, method, url, **kwargs):
    try:
        response = http.request(method, url, **kwargs)
    except httpx.HTTPError:
        raise LurkerError("NETWORK_ERROR", "网络请求失败", retryable=True) from None
    check_response(response)
    return response


def object_json(response):
    try:
        data = response.json()
    except ValueError:
        raise LurkerError("RESPONSE_INVALID", "服务返回无效 JSON") from None
    require(isinstance(data, dict), "RESPONSE_INVALID", "服务响应必须为对象")
    return data


def public_https(url, domains=None):
    require(isinstance(url, str), "UNSAFE_URL", "媒体地址无效")
    parsed = urlparse(url)
    require(
        parsed.scheme == "https"
        and parsed.hostname
        and not parsed.username
        and not parsed.password
        and parsed.port in (None, 443),
        "UNSAFE_URL",
        "仅接受 HTTPS 公共媒体地址",
    )
    if domains:
        require(
            any(parsed.hostname == d or parsed.hostname.endswith("." + d) for d in domains),
            "UNSAFE_URL",
            "响应指向非预期服务域名",
        )
    return url
