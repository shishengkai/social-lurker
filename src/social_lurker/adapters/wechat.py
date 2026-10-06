from dataclasses import replace

from ..errors import LurkerError, require
from ..transport import WX
from .base import Author, Page, Work, identity, public_url, text, timestamp
from .douyin import cover, provider_id


def parse_work(item):
    require(isinstance(item, dict))
    # raw=false fields are documented. No fallback into raw media/decoding structures.
    aid = identity(item.get("username"))
    return Work(
        "wechat_channels",
        provider_id(item.get("id")),
        aid,
        text(item.get("title")),
        timestamp(item.get("create_time")),
        public_url(item.get("share_url"), ("weixin.qq.com",)),
        cover(item.get("cover_url") or item.get("cover_img_url")),
        "unknown",
    )


def parse_page(data, author):
    require(isinstance(data, dict))
    require(data.get("username") == author.author_id, "PAGE_IDENTITY_INVALID")
    videos = data.get("videos")
    require(isinstance(videos, list) and type(data.get("count")) is int and data["count"] == len(videos))
    more = data.get("up_continue")
    require(type(more) in (int, bool) and more in (0, 1))
    cursor = data.get("last_buffer") if more else None
    require(not more or isinstance(cursor, str) and bool(cursor), "CURSOR_INVALID")
    page = Page([parse_work(item) for item in videos], cursor, bool(more))
    page.validated(author)
    return page


class Wechat:
    def __init__(self, transport):
        self.transport = transport

    def resolve_author(self, link):
        data = self.transport.call(WX + "fetch_video_detail", {"share_url": link, "raw": False})
        parsed = parse_work(data)
        return Author("wechat_channels", parsed.author_id, text(data.get("nickname")) or parsed.author_id)

    def fetch_page(self, author, cursor):
        data = self.transport.call(
            WX + "fetch_user_videos",
            {"username": author.author_id, "last_buffer": cursor or "", "raw": False},
        )
        return parse_page(data, author)

    def enrich_metadata(self, author, work):
        if work.title is None or work.published_at is None:
            try:
                data = self.transport.call(
                    WX + "fetch_video_detail", {"object_id": work.work_id, "raw": False}
                )
                value = parse_work(data)
                value.validate(author)
                require(value.work_id == work.work_id, "PAGE_IDENTITY_INVALID")
                work = replace(
                    work,
                    title=value.title or work.title,
                    published_at=value.published_at or work.published_at,
                    url=value.url or work.url,
                )
            except LurkerError as error:
                if error.global_stop or error.code in {
                    "PAGE_IDENTITY_INVALID",
                    "OUTPUT_CLOSED",
                    "INTERRUPTED",
                }:
                    raise
        if work.url is None:
            try:
                data = self.transport.call(
                    WX + "fetch_video_share_url", {"object_id": work.work_id, "raw": False}
                )
                require(provider_id(data.get("object_id")) == work.work_id, "PAGE_IDENTITY_INVALID")
                work = replace(work, url=public_url(data.get("share_url"), ("weixin.qq.com",)))
            except LurkerError as error:
                if error.global_stop or error.code in {
                    "PAGE_IDENTITY_INVALID",
                    "OUTPUT_CLOSED",
                    "INTERRUPTED",
                }:
                    raise
        return work
