from dataclasses import replace
from urllib.parse import quote

from ..errors import require
from ..transport import DY
from .base import Author, Page, Work, identity, public_url, text, timestamp
from .links import resolve_douyin_link


def provider_id(value):
    if type(value) is int and value > 0:
        value = str(value)  # Python JSON preserves 64-bit integers exactly; public output is string.
    return identity(value)


def detail(data):
    one, many = data.get("aweme_detail"), data.get("aweme_details")
    require(one is None or isinstance(one, dict))
    require(many is None or isinstance(many, list))
    items = ([one] if one else []) + (many or [])
    require(len(items) == 1 and isinstance(items[0], dict))
    return items[0]


def cover(value):
    if isinstance(value, str):
        return public_url(value)
    if isinstance(value, dict) and isinstance(value.get("url_list"), list):
        return next((url for v in value["url_list"] if (url := public_url(v))), None)
    return None


def parse_work(item):
    require(isinstance(item, dict))
    user = item.get("author")
    require(isinstance(user, dict), "PAGE_IDENTITY_INVALID")
    video = item.get("video") or {}
    require(isinstance(video, dict))
    aweme_type = item.get("aweme_type")
    kind = "image" if aweme_type in (68, 150) else "video" if aweme_type == 0 else "unknown"
    thumb = cover(video.get("cover"))
    images = item.get("images")
    if not thumb and isinstance(images, list) and images:
        thumb = cover(images[0])
    return Work(
        "douyin",
        provider_id(item.get("aweme_id")),
        identity(user.get("sec_uid")),
        text(item.get("desc")),
        timestamp(item.get("create_time")),
        public_url(item.get("share_url"), ("douyin.com", "iesdouyin.com")),
        thumb,
        kind,
    )


def parse_page(data, author):
    require(isinstance(data, dict))
    require(data.get("sec_uid") == author.author_id, "PAGE_IDENTITY_INVALID")
    require(isinstance(data.get("aweme_list"), list))
    more = data.get("has_more")
    require(type(more) in (int, bool) and more in (0, 1))
    items, excluded, ownership = [], [], {}
    for raw in data["aweme_list"]:
        item = parse_work(raw)
        require(
            item.work_id not in ownership or ownership[item.work_id] == item.author_id,
            "PAGE_IDENTITY_INVALID",
        )
        ownership[item.work_id] = item.author_id
        if item.author_id != author.author_id:
            cooperation = raw.get("cooperation_info")
            creators = cooperation.get("co_creators") if isinstance(cooperation, dict) else None
            require(
                isinstance(creators, list)
                and any(isinstance(c, dict) and c.get("sec_uid") == author.author_id for c in creators),
                "PAGE_IDENTITY_INVALID",
            )
            excluded.append(item.work_id)
        else:
            items.append(item)
    cursor = provider_id(data.get("max_cursor")) if more else None
    require(not more or cursor.isascii() and cursor.isdigit(), "CURSOR_INVALID")
    page = Page(items, cursor, bool(more), tuple(sorted(set(excluded))))
    page.validated(author)
    return page


class Douyin:
    def __init__(self, transport):
        self.transport = transport

    def resolve_author(self, link):
        kind, resolved_id = resolve_douyin_link(link, self.transport.redirect)
        if kind == "user":
            aid = identity(resolved_id)
            data = self.transport.call(DY + "handler_user_profile", {"sec_user_id": aid})
            user = data.get("user")
            require(isinstance(user, dict) and user.get("sec_uid") == aid, "PAGE_IDENTITY_INVALID")
        else:
            data = self.transport.call(DY + "fetch_one_video_by_share_url", {"share_url": link})
            item = detail(data)
            parsed = parse_work(item)
            aid, user = parsed.author_id, item["author"]
        return Author(
            "douyin",
            aid,
            text(user.get("nickname")) or aid,
            "https://www.douyin.com/user/" + quote(aid, safe=""),
        )

    def fetch_page(self, author, cursor):
        data = self.transport.call(
            DY + "fetch_user_post_videos",
            {
                "sec_user_id": author.author_id,
                "max_cursor": cursor or "0",
                "count": 20,
                "sort_type": 0,
                "channel": "normal",
            },
        )
        return parse_page(data, author)

    def enrich_metadata(self, author, work):
        if work.title is not None and work.published_at is not None and work.url is not None:
            return work
        data = self.transport.call(DY + "fetch_one_video", {"aweme_id": work.work_id})
        value = parse_work(detail(data))
        value.validate(author)
        require(value.work_id == work.work_id, "PAGE_IDENTITY_INVALID")
        return replace(
            work,
            title=value.title or work.title,
            published_at=value.published_at or work.published_at,
            url=value.url or work.url,
            kind=value.kind if value.kind != "unknown" else work.kind,
        )
