"""Metadata-only TikHub adapters. Real-host coverage evidence is separate from fixtures."""

import re
from dataclasses import dataclass, replace
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from .errors import LurkerError, require
from .util import clean_text, platform_id, public_cover_url, public_url

DY = "/douyin/app/v3/"
WX = "/wechat_channels/v2/"
ENDPOINTS = {
    DY + "fetch_one_video_by_share_url": "GET",
    DY + "fetch_one_video": "GET",
    DY + "handler_user_profile": "GET",
    DY + "fetch_user_post_videos": "GET",
    WX + "fetch_video_detail": "POST",
    WX + "fetch_channel_id_to_username": "POST",
    WX + "fetch_user_profile": "POST",
    WX + "fetch_user_videos": "POST",
    WX + "fetch_video_share_url": "POST",
}
ADAPTER_VERSION = "metadata-r1.3"


@dataclass(frozen=True)
class Author:
    platform: str
    author_id: str
    author_name: str
    profile_url: str | None = None
    variant: str = "default"


@dataclass(frozen=True)
class Publication:
    platform: str
    work_id: str
    author_id: str
    author_name: str
    published_at: int | None
    title: str = ""
    source_url: str | None = None
    cover_url: str | None = None
    kind: str = "video"

    def __post_init__(self):
        platform_id(self.work_id)
        platform_id(self.author_id)
        require(self.platform in {"douyin", "wechat_channels"}, "PLATFORM_UNSUPPORTED")
        require(
            self.published_at is None
            or (type(self.published_at) is int and 0 < self.published_at < 4102444800),
            "METADATA_TIME_INVALID",
        )
        require(isinstance(self.title, str) and isinstance(self.author_name, str), "METADATA_INVALID")


@dataclass(frozen=True)
class Page:
    items: list[Publication]
    next_cursor: str | None
    end_state: str
    order_hint: str = "latest"


def timestamp(value):
    return value if type(value) is int and 0 < value < 4102444800 else None


def wx_detail(data):
    require(isinstance(data, dict), "RESPONSE_INVALID")
    if data.get("id") is not None:
        return data
    if data.get("message") and not data.get("objects"):
        raise LurkerError(
            "WECHAT_DETAIL_UNAVAILABLE",
            "视频号详情未返回作品身份，不能据此确认作者",
            next_action="retry_share_link",
        )
    objects = data.get("objects")
    require(
        isinstance(objects, list) and len(objects) == 1 and isinstance(objects[0], dict),
        "PAGE_IDENTITY_INVALID",
    )
    return objects[0]


def douyin_detail(data):
    """Extract one work, or explain a provider-filtered empty response without retrying."""
    require(isinstance(data, dict), "RESPONSE_INVALID")
    single, many = data.get("aweme_detail"), data.get("aweme_details")
    require(single is None or isinstance(single, dict), "PAGE_PROTOCOL_INVALID")
    require(many is None or isinstance(many, list), "PAGE_PROTOCOL_INVALID")
    items = ([single] if single else []) + (many or [])
    if items:
        require(len(items) == 1 and isinstance(items[0], dict), "PAGE_IDENTITY_INVALID")
        return items[0]
    filters = data.get("filter_list")
    reasons = {
        item["reason"]
        for item in (filters if isinstance(filters, list) else [])
        if isinstance(item, dict) and type(item.get("reason")) is int
    }
    if 5 in reasons:
        message = "数据接口将此作品标记为私密，未返回作品资料；请换一条公开作品链接"
    elif 10 in reasons:
        message = "数据接口将此作品标记为部分可见，未返回作品资料；请换一条公开作品链接"
    elif 8 in reasons:
        message = "数据接口提示作品不可用或受版权限制；请换一条公开作品链接"
    else:
        message = "数据接口未返回可用的抖音作品资料；请核对链接后再试"
    raise LurkerError("DOUYIN_DETAIL_UNAVAILABLE", message, next_action="provide_public_share_link")


def cover(value):
    if isinstance(value, str):
        return public_cover_url(value)
    if isinstance(value, dict):
        urls = value.get("url_list", [])
        if isinstance(urls, list):
            return next((public_cover_url(u) for u in urls if public_cover_url(u)), None)
    return None


def wechat_cover(media):
    value = cover(media.get("cover_url"))
    suffix = media.get("cover_url_token")
    if not value or not suffix:
        return value
    u = urlsplit(value)
    if u.hostname != "wxapp.tc.qq.com" or not isinstance(suffix, str):
        return value
    if any(k == "token" for k, _ in parse_qsl(u.query)):
        return value
    pairs = parse_qsl(suffix.lstrip("&?"), keep_blank_values=True)
    if len(pairs) != 1 or pairs[0][0] != "token":
        return None
    candidate = u._replace(query=u.query + ("&" if u.query else "") + urlencode(pairs)).geturl()
    return public_cover_url(candidate)


def publication(platform, item, author_id=None, author_name="", *, from_list=True):
    require(isinstance(item, dict), "PAGE_PROTOCOL_INVALID")
    if platform == "douyin":
        author = item.get("author") or {}
        require(isinstance(author, dict), "PAGE_IDENTITY_INVALID")
        aid = platform_id(author.get("sec_uid") or author_id)
        kind = "image" if item.get("images") or item.get("aweme_type") in (68, 150) else "video"
        video = item.get("video") or {}
        require(isinstance(video, dict), "PAGE_PROTOCOL_INVALID")
        share = public_url(item.get("share_url"), ("douyin.com", "iesdouyin.com"))
        thumb = cover(video.get("cover")) if from_list else None
        if not thumb and from_list and kind == "image":
            images = item.get("images")
            if isinstance(images, list) and images:
                thumb = cover(images[0])
        return Publication(
            platform,
            platform_id(item.get("aweme_id")),
            aid,
            clean_text(author.get("nickname") or author_name or aid, 512),
            timestamp(item.get("create_time")),
            item.get("desc") or "",
            share,
            thumb,
            kind,
        )
    contact = item.get("contact") or {}
    desc = item.get("objectDesc") or {}
    require(isinstance(contact, dict) and isinstance(desc, dict), "PAGE_IDENTITY_INVALID")
    aid = platform_id(item.get("username") or contact.get("username") or author_id)
    require(
        not item.get("username") or not contact.get("username") or item["username"] == contact["username"],
        "PAGE_IDENTITY_INVALID",
    )
    url = next(
        (
            public_url(item.get(k), ("weixin.qq.com",))
            for k in ("share_url", "shareUrl", "short_url")
            if public_url(item.get(k), ("weixin.qq.com",))
        ),
        None,
    )
    thumb = (
        next(
            (value for k in ("cover_img_url", "cover_url", "thumb_url") if (value := cover(item.get(k)))),
            None,
        )
        if from_list
        else None
    )
    if not thumb and from_list:
        media = item.get("media") or desc.get("media") or []
        if isinstance(media, dict):
            thumb = wechat_cover(media)
        if isinstance(media, list) and media and isinstance(media[0], dict):
            thumb = cover(media[0].get("thumb_url") or media[0].get("thumbUrl"))
    return Publication(
        platform,
        platform_id(item.get("id")),
        aid,
        clean_text(item.get("nickname") or contact.get("nickname") or author_name or aid, 512),
        timestamp(item.get("create_time") or item.get("createtime")),
        item.get("title") or desc.get("description") or "",
        url,
        thumb,
    )


class TikHub:
    def __init__(self, client):
        self.client = client

    def capability(self, settings, platform, variant):
        evidence = settings["host"].get("evidence_ref") or {}
        item = evidence.get("sources", {}).get(platform + ":" + variant, {})
        if item.get("adapter_version") != ADAPTER_VERSION or not item.get("evidence"):
            return "unverified"
        level = item.get("level", "unverified")
        return (
            level
            if level in {"recent_pages_verified", "enumeration_verified", "range_verified"}
            else "unverified"
        )

    def list_endpoint(self, watch):
        return (
            (DY + "fetch_user_post_videos") if watch["platform"] == "douyin" else (WX + "fetch_user_videos")
        )

    def wechat_detail(self, params, **request):
        data = self.client.call(WX + "fetch_video_detail", {**params, "raw": False}, **request)
        try:
            return wx_detail(data)
        except LurkerError as error:
            if error.code != "WECHAT_DETAIL_UNAVAILABLE":
                raise
        # The provider's simplified response can be a successful envelope containing only
        # an error message. One same-endpoint raw retry is metadata-only and shares all gates.
        return wx_detail(self.client.call(WX + "fetch_video_detail", {**params, "raw": True}, **request))

    def resolve_author(self, source, *, platform=None, author_id=None, channel_id=None, **request):
        if channel_id is not None:
            require(platform == "wechat_channels" and author_id is None and not source, "INPUT_INVALID")
            data = self.client.call(
                WX + "fetch_channel_id_to_username",
                {"channel_id": platform_id(channel_id), "raw": False},
                **request,
            )
            author_id = platform_id(data.get("username"))
        if author_id is not None:
            require(platform in {"douyin", "wechat_channels"}, "PLATFORM_UNSUPPORTED")
            aid = platform_id(author_id)
        else:
            require(isinstance(source, str), "INPUT_INVALID")
            urls = re.findall(r"https://[^\s<>]+", source)
            require(len(urls) == 1, "SOURCE_LINK_REQUIRED")
            source = urls[0].rstrip("。，；！）")
            require(public_url(source, ("douyin.com", "weixin.qq.com")), "SOURCE_LINK_INVALID")
            u = urlsplit(source)
            platform = "wechat_channels" if u.hostname.endswith("weixin.qq.com") else "douyin"
            if platform == "douyin" and u.path.startswith("/user/"):
                aid = platform_id(u.path.split("/")[2])
            else:
                endpoint = (
                    WX + "fetch_video_detail"
                    if platform == "wechat_channels"
                    else DY + "fetch_one_video_by_share_url"
                )
                if platform == "wechat_channels":
                    item = self.wechat_detail({"share_url": source}, **request)
                else:
                    data = self.client.call(endpoint, {"share_url": source}, **request)
                    item = douyin_detail(data)
                parsed = publication(platform, item, from_list=False)
                aid = parsed.author_id
                if platform == "wechat_channels" and parsed.author_name != aid:
                    # This share's resolved work already provides the author's identity and
                    # name. A redundant profile request can fail independently and costs more.
                    return Author(platform, aid, parsed.author_name)
        if platform == "douyin":
            data = self.client.call(DY + "handler_user_profile", {"sec_user_id": aid}, **request)
            user = data.get("user") or {}
            require(user.get("sec_uid") == aid, "PAGE_IDENTITY_INVALID")
            return Author(
                platform,
                aid,
                clean_text(user.get("nickname") or aid, 512),
                "https://www.douyin.com/user/" + quote(aid, safe=""),
                "normal",
            )
        data = self.client.call(WX + "fetch_user_profile", {"username": aid, "raw": False}, **request)
        if data.get("message") and not data.get("username"):
            raise LurkerError("WECHAT_PROFILE_UNAVAILABLE", "视频号作者资料暂未返回有效身份，请稍后重试")
        require(data.get("username") == aid, "PAGE_IDENTITY_INVALID")
        return Author(platform, aid, clean_text(data.get("nickname") or aid, 512))

    def list_publications(self, watch, cursor=None, **request):
        aid = watch["author_id"]
        platform = watch["platform"]
        params = (
            {
                "sec_user_id": aid,
                "max_cursor": cursor or "0",
                "count": 20,
                "sort_type": 0,
                "channel": watch["source_variant"],
            }
            if platform == "douyin"
            else {"username": aid, "last_buffer": cursor or "", "raw": False}
        )
        data = self.client.call(
            self.list_endpoint(watch), params, watch=(watch["id"], watch["generation"]), **request
        )
        if platform == "douyin":
            require(
                data.get("sec_uid") == aid and isinstance(data.get("aweme_list"), list),
                "PAGE_IDENTITY_INVALID",
            )
            require(
                type(data.get("has_more")) in (int, bool) and data["has_more"] in (0, 1),
                "PAGE_PROTOCOL_INVALID",
            )
            items = [publication(platform, i, aid, watch["author_name"]) for i in data["aweme_list"]]
            more = bool(data["has_more"])
            raw = data.get("max_cursor")
            cursor_out = platform_id(raw) if more and raw is not None else None
        else:
            require(
                data.get("username") == aid and isinstance(data.get("videos"), list), "PAGE_IDENTITY_INVALID"
            )
            items = [publication(platform, i, aid, watch["author_name"]) for i in data["videos"]]
            # This exact response shape still requires live evidence before automatic use.
            ended = not items and data.get("count") == 0 and data.get("up_continue") == 0
            more = not ended
            raw = data.get("last_buffer")
            require(raw is None or isinstance(raw, str), "CURSOR_INVALID")
            cursor_out = raw if more else None
        require(all(i.author_id == aid for i in items), "PAGE_IDENTITY_INVALID")
        require(not more or (items and cursor_out), "PAGE_PROTOCOL_INVALID")
        return Page(items, cursor_out, "more" if more else "confirmed_end")

    def complete_metadata(self, watch, row, **request):
        request["watch"] = (watch["id"], watch["generation"])
        request["failure_count"] = row["failure_count"]
        p = Publication(
            row["platform"],
            row["work_id"],
            watch["author_id"],
            row["author_name"],
            row["published_at"],
            row["title"],
            row["source_url"],
        )
        if p.published_at is None or (p.platform == "douyin" and not p.source_url):
            if p.platform == "wechat_channels":
                detail = self.wechat_detail({"object_id": p.work_id}, **request)
            else:
                data = self.client.call(DY + "fetch_one_video", {"aweme_id": p.work_id}, **request)
                detail = douyin_detail(data)
            p = publication(p.platform, detail, from_list=False)
            require(
                p.work_id == row["work_id"] and p.author_id == watch["author_id"], "PAGE_IDENTITY_INVALID"
            )
        if p.platform == "wechat_channels" and not p.source_url:
            data = self.client.call(
                WX + "fetch_video_share_url", {"object_id": p.work_id, "raw": False}, **request
            )
            require(platform_id(data.get("object_id")) == p.work_id, "PAGE_IDENTITY_INVALID")
            p = replace(p, source_url=public_url(data.get("share_url"), ("weixin.qq.com",)))
        # No detail response is permitted to introduce a cover or media field.
        return replace(p, cover_url=None)


def validate_params(endpoint, params):
    """Only known endpoint parameters; explicit recovery is not an arbitrary request proxy."""
    require(endpoint in ENDPOINTS and isinstance(params, dict), "ENDPOINT_INVALID")
    suffix = endpoint.rsplit("/", 1)[1]
    contracts = {
        "fetch_one_video_by_share_url": ({"share_url"}, set()),
        "fetch_one_video": ({"aweme_id"}, set()),
        "handler_user_profile": ({"sec_user_id"}, set()),
        "fetch_user_post_videos": ({"sec_user_id"}, {"max_cursor", "count", "sort_type", "channel"}),
        "fetch_video_detail": (set(), {"share_url", "object_id", "raw"}),
        "fetch_channel_id_to_username": ({"channel_id"}, {"raw"}),
        "fetch_user_profile": ({"username"}, {"raw"}),
        "fetch_user_videos": ({"username"}, {"raw", "last_buffer"}),
        "fetch_video_share_url": ({"object_id"}, {"raw"}),
    }
    required, optional = contracts[suffix]
    require(required <= set(params) <= required | optional, "REQUEST_PARAMS_INVALID")
    if suffix == "fetch_video_detail":
        require(("share_url" in params) ^ ("object_id" in params), "REQUEST_PARAMS_INVALID")
    for key, value in params.items():
        if key == "raw":
            require(
                value is False or (suffix == "fetch_video_detail" and value is True), "REQUEST_PARAMS_INVALID"
            )
        elif key == "share_url":
            require(public_url(value, ("douyin.com", "weixin.qq.com")), "SOURCE_LINK_INVALID")
        elif key == "last_buffer":
            require(isinstance(value, str) and len(value.encode()) <= 16384, "CURSOR_INVALID")
        elif key == "max_cursor":
            require(
                (type(value) is int and value >= 0)
                or (isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 32),
                "CURSOR_INVALID",
            )
        elif key == "count":
            require(type(value) is int and 1 <= value <= 20, "REQUEST_PARAMS_INVALID")
        elif key == "sort_type":
            require(value == 0, "REQUEST_PARAMS_INVALID")
        elif key == "channel":
            require(value in {"normal", "lite"}, "REQUEST_PARAMS_INVALID")
        else:
            platform_id(value)


def validate_probe(endpoint, data, params):
    """A syntactically successful envelope is insufficient evidence of endpoint recovery."""
    suffix = endpoint.rsplit("/", 1)[1]
    if suffix == "fetch_user_post_videos":
        require(
            data.get("sec_uid") == params["sec_user_id"] and isinstance(data.get("aweme_list"), list),
            "RESPONSE_INVALID",
        )
        require(type(data.get("has_more")) in (int, bool) and data["has_more"] in (0, 1), "RESPONSE_INVALID")
        for item in data["aweme_list"]:
            require(
                publication("douyin", item, params["sec_user_id"]).author_id == params["sec_user_id"],
                "PAGE_IDENTITY_INVALID",
            )
        if data["has_more"]:
            platform_id(data.get("max_cursor"))
    elif suffix == "fetch_user_videos":
        require(
            data.get("username") == params["username"] and isinstance(data.get("videos"), list),
            "RESPONSE_INVALID",
        )
        for item in data["videos"]:
            require(
                publication("wechat_channels", item, params["username"]).author_id == params["username"],
                "PAGE_IDENTITY_INVALID",
            )
        require(
            (not data["videos"] and data.get("count") == 0 and data.get("up_continue") == 0)
            or isinstance(data.get("last_buffer"), str)
            and bool(data["last_buffer"]),
            "RESPONSE_INVALID",
        )
    elif suffix == "handler_user_profile":
        require(
            isinstance(data.get("user"), dict) and data["user"].get("sec_uid") == params["sec_user_id"],
            "PAGE_IDENTITY_INVALID",
        )
    elif suffix == "fetch_user_profile":
        require(data.get("username") == params["username"], "PAGE_IDENTITY_INVALID")
    elif suffix in {"fetch_video_detail", "fetch_one_video", "fetch_one_video_by_share_url"}:
        p = (
            publication("wechat_channels", wx_detail(data), from_list=False)
            if suffix == "fetch_video_detail"
            else publication("douyin", douyin_detail(data), from_list=False)
        )
        if "object_id" in params or "aweme_id" in params:
            require(p.work_id == params.get("object_id", params.get("aweme_id")), "PAGE_IDENTITY_INVALID")
    elif suffix == "fetch_video_share_url":
        require(
            platform_id(data.get("object_id")) == params["object_id"]
            and public_url(data.get("share_url"), ("weixin.qq.com",)),
            "RESPONSE_INVALID",
        )
    elif suffix == "fetch_channel_id_to_username":
        platform_id(data.get("username"))
