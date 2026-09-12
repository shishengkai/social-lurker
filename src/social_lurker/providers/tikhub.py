from dataclasses import dataclass, field, replace
from urllib.parse import quote, urlparse

from ..errors import LurkerError, require
from .http import client, object_json, public_https, request

BASE = "https://api.tikhub.io/api/v1"
WX = BASE + "/wechat_channels/v2/"
DY = BASE + "/douyin/app/v3/"


@dataclass(frozen=True)
class Account:
    platform: str
    id: str
    name: str
    profile_url: str
    reported_total: int | None = None


@dataclass(frozen=True)
class WorkMetadata:
    id: str
    title: str
    source_url: str
    published_at: float | None
    caption: str = ""
    # Only in memory. Never persist provider URLs/keys in business records.
    media: dict = field(default_factory=dict, repr=False)
    author_id: str | None = None
    pinned: bool | None = None


@dataclass(frozen=True)
class Page:
    items: list[WorkMetadata]
    cursor: str | None
    has_more: bool | None
    ordering_basis: str = "unknown"


def stable_id(value):
    require(
        (isinstance(value, str) and bool(value)) or (type(value) is int and value > 0),
        "IDENTITY_INVALID",
        "缺少稳定平台标识",
    )
    return str(value)


def timestamp(value):
    if type(value) in (int, float) and 0 < value < 4102444800:
        return float(value)
    return None


def wx_object(data):
    if data.get("id"):
        return data
    objects = data.get("objects")
    if isinstance(objects, list) and len(objects) == 1 and isinstance(objects[0], dict):
        return objects[0]
    raise LurkerError("RESPONSE_INVALID", "视频号详情缺少作品身份")


def wechat_source_url(value):
    # No invented deep-link route: only preserve URLs actually returned by the provider.
    for key in ("share_url", "shareUrl", "short_url", "url"):
        url = value.get(key)
        if (
            isinstance(url, str)
            and url.startswith("https://")
            and urlparse(url).hostname in ("weixin.qq.com", "channels.weixin.qq.com")
        ):
            return url
    return ""


def wx_work(value):
    ident = stable_id(value.get("id"))
    desc = value.get("objectDesc") or {}
    require(isinstance(desc, dict), "RESPONSE_INVALID", "视频号作品结构无效")
    title = value.get("title") or desc.get("description") or ""
    require(isinstance(title, str), "RESPONSE_INVALID", "视频号文案字段无效")
    media = value.get("media") or desc.get("media") or []
    if isinstance(media, list):
        media = media[0] if media else {}
    require(isinstance(media, dict), "RESPONSE_INVALID", "视频号媒体结构无效")
    author = value.get("username") or (value.get("contact") or {}).get("username")
    return WorkMetadata(
        ident,
        title,
        wechat_source_url(value),
        timestamp(value.get("create_time") or value.get("createtime")),
        title,
        media,
        stable_id(author) if author else None,
    )


def dy_work(value):
    ident = stable_id(value.get("aweme_id"))
    caption = value.get("desc", "")
    require(isinstance(caption, str), "RESPONSE_INVALID", "抖音文案字段无效")
    author = (value.get("author") or {}).get("sec_uid")
    video = value.get("video") or {}
    require(isinstance(video, dict), "RESPONSE_INVALID", "抖音媒体结构无效")
    # Image galleries/music/live containers are not spoken-video substitutes.
    if value.get("images") or value.get("aweme_type") in (68, 150):
        video = {}
    return WorkMetadata(
        ident,
        caption,
        "https://www.douyin.com/video/" + quote(ident),
        timestamp(value.get("create_time")),
        caption,
        video,
        stable_id(author) if author else None,
        bool(value.get("is_top")),
    )


class TikHub:
    def __init__(self, key, http=None):
        require(bool(key), "CREDENTIALS_MISSING", "请在当前实例 .env 配置 TIKHUB_API_KEY")
        self.http = http or client()
        self.headers = {"Authorization": "Bearer " + key}

    def call(self, platform, endpoint, payload):
        method = "POST" if platform == "wechat_channels" else "GET"
        response = request(
            self.http,
            method,
            (WX if method == "POST" else DY) + endpoint,
            headers=self.headers,
            **({"json": payload} if method == "POST" else {"params": payload}),
        )
        obj = object_json(response)
        code = obj.get("code")
        if code in (401, 403, 402, 429):
            raise LurkerError(
                {401: "AUTH_REQUIRED", 403: "AUTH_REQUIRED", 402: "QUOTA_EXHAUSTED", 429: "RATE_LIMITED"}[
                    code
                ],
                "TikHub 拒绝业务请求",
                retryable=code == 429,
            )
        require(code == 200 and isinstance(obj.get("data"), dict), "RESPONSE_INVALID", "TikHub 业务响应无效")
        data = obj["data"]
        if "message" in data and set(data) <= {"message", "debug_id", "debug_info"}:
            raise LurkerError("UPSTREAM_TEMPORARY", "TikHub 暂时未返回业务数据", retryable=True)
        require(data.get("status_code", 0) == 0, "RESPONSE_INVALID", "平台返回业务错误")
        return data

    def resolve(self, source):
        public_https(source, ["weixin.qq.com", "douyin.com"])
        host = urlparse(source).hostname
        if host == "weixin.qq.com":
            data = wx_object(
                self.call("wechat_channels", "fetch_video_detail", {"share_url": source, "raw": True})
            )
            ident = stable_id(data.get("username") or (data.get("contact") or {}).get("username"))
            profile = self.call("wechat_channels", "fetch_user_profile", {"username": ident, "raw": False})
            require(profile.get("username") == ident, "IDENTITY_MISMATCH", "视频号详情与账号资料身份不一致")
            return Account(
                "wechat_channels",
                ident,
                str(profile.get("nickname") or data.get("nickname") or ident),
                "",
                profile.get("feeds_count"),
            )
        path = urlparse(source).path
        if host in ("www.douyin.com", "douyin.com") and path.startswith("/user/"):
            ident = stable_id(path.split("/")[2])
        else:
            data = self.call("douyin", "fetch_one_video_by_share_url", {"share_url": source})
            detail = data.get("aweme_detail") or {}
            stable_id(detail.get("aweme_id"))
            ident = stable_id((detail.get("author") or {}).get("sec_uid"))
        data = self.call("douyin", "handler_user_profile", {"sec_user_id": ident})
        profile = data.get("user") or {}
        require(profile.get("sec_uid") == ident, "IDENTITY_MISMATCH", "抖音作品与账号资料身份不一致")
        return Account(
            "douyin",
            ident,
            str(profile.get("nickname") or ident),
            "https://www.douyin.com/user/" + quote(ident),
            profile.get("aweme_count"),
        )

    def page(self, account, cursor=None):
        platform = account["platform"]
        ident = account["platform_account_id"]
        if platform == "wechat_channels":
            data = self.call(
                platform, "fetch_user_videos", {"username": ident, "last_buffer": cursor or "", "raw": False}
            )
            require(
                data.get("username") == ident and isinstance(data.get("videos"), list),
                "RESPONSE_INVALID",
                "视频号列表身份或列表字段缺失",
            )
            items = [wx_work(v) for v in data["videos"]]
            next_cursor = data.get("last_buffer")
            require(next_cursor is None or isinstance(next_cursor, str), "RESPONSE_INVALID", "视频号游标无效")
            # Observed up_continue=0 alongside usable cursors; it is not an end-of-list proof.
            # Live tail: videos=[], count=0, up_continue=0; last_buffer still contains an opaque cursor.
            # Nonempty pages never terminate on this flag; all three empty-tail signals are required.
            more = False if not items and data.get("count") == 0 and data.get("up_continue") == 0 else None
        else:
            data = self.call(
                platform,
                "fetch_user_post_videos",
                {
                    "sec_user_id": ident,
                    "max_cursor": cursor or "0",
                    "count": 20,
                    "sort_type": 0,
                    "channel": "normal",
                },
            )
            require(
                data.get("sec_uid") == ident and isinstance(data.get("aweme_list"), list),
                "RESPONSE_INVALID",
                "抖音列表身份或列表字段缺失",
            )
            items = [dy_work(v) for v in data["aweme_list"]]
            next_cursor = str(data["max_cursor"]) if data.get("max_cursor") is not None else None
            flag = data.get("has_more")
            more = bool(flag) if type(flag) in (bool, int) and flag in (0, 1) else None
        require(all(w.author_id in (None, ident) for w in items), "IDENTITY_MISMATCH", "列表混入其他账号作品")
        return Page(items, next_cursor or None, more)

    def detail(self, account, work):
        if account["platform"] == "wechat_channels":
            data = self.call(
                "wechat_channels", "fetch_video_detail", {"object_id": work["platform_work_id"], "raw": True}
            )
            result = wx_work(wx_object(data))
        else:
            data = self.call("douyin", "fetch_one_video_by_share_url", {"share_url": work["source_url"]})
            result = dy_work(data.get("aweme_detail") or {})
        require(
            result.id == work["platform_work_id"] and result.author_id == account["platform_account_id"],
            "IDENTITY_MISMATCH",
            "媒体详情与账号或作品身份不一致",
        )
        if not result.source_url:
            existing = work.get("source_url")
            if existing:
                result = replace(result, source_url=existing)
            elif account["platform"] == "wechat_channels":
                share = self.call(
                    "wechat_channels", "fetch_video_share_url", {"object_id": result.id, "raw": False}
                )
                require(
                    str(share.get("object_id")) == result.id, "IDENTITY_MISMATCH", "分享链接作品 ID 不匹配"
                )
                url = public_https(share.get("share_url"), ["weixin.qq.com"])
                result = replace(result, source_url=url)
        return result

    @staticmethod
    def media_source(platform, detail):
        media = detail.media
        require(media, "UNSUPPORTED_MEDIA", "作品不是可处理的完整口播视频")
        if platform == "wechat_channels":
            url = media.get("full_url") or media.get("fullUrl")
            if url:
                url += media.get("fullUrlToken") or media.get("full_url_token") or ""
            if not url:
                url = (media.get("url") or "") + (media.get("urlToken") or media.get("url_token") or "")
            key = media.get("decodeKey") or media.get("decode_key")
            duration = media.get("videoPlayLen") or media.get("video_play_len")
            size = media.get("fileSize") or media.get("file_size")
        else:
            # Never select music.play_url: it may be unrelated background music.
            urls = (media.get("play_addr") or {}).get("url_list") or []
            require(isinstance(urls, list) and urls, "UNSUPPORTED_MEDIA", "作品缺少视频播放地址")
            url = urls[0]
            key = None
            duration = media.get("duration")
            duration = duration / 1000 if isinstance(duration, (float, int)) else None
            size = (media.get("play_addr") or {}).get("data_size")
        if isinstance(url, str) and url.startswith("http://") and urlparse(url).hostname == "wxapp.tc.qq.com":
            url = "https://" + url[len("http://") :]
        public_https(url)
        return {
            "url": url,
            "decode_key": str(key) if key else None,
            "duration": duration if type(duration) in (float, int) and duration > 0 else None,
            "size": size if type(size) is int and size > 0 else None,
        }
