"""Bilibili video submissions, fixed newest-first V2 pagination."""

import re
from urllib.parse import urlsplit

from ..errors import require
from ..transport import BL
from .base import Author, Page, Work, text, timestamp
from .common import metadata_url, positive_id
from .links import resolve_bilibili_link

BV = re.compile(r"BV[0-9A-Za-z]{10}")


def envelope(data):
    require(isinstance(data, dict) and type(data.get("code")) is int and data["code"] == 0)
    require(isinstance(data.get("data"), dict))
    return data["data"]


def bvid(value):
    require(isinstance(value, str) and BV.fullmatch(value), "PAGE_IDENTITY_INVALID")
    return value


def parse_page(data, author, number, verified_exclusions=()):
    data = envelope(data)
    entries, paging = data.get("archives"), data.get("page")
    require(isinstance(entries, list) and isinstance(paging, dict))
    require(type(paging.get("num")) is int and paging["num"] == number, "CURSOR_INVALID")
    total, size = paging.get("total"), paging.get("size")
    require(type(total) is int and total >= 0 and type(size) is int and 1 <= size <= 100)
    require(len(entries) <= size and total >= len(entries))
    more = number * size < total
    items, excluded = [], []
    for raw in entries:
        require(isinstance(raw, dict))
        bid, aid = bvid(raw.get("bvid")), positive_id(raw.get("aid"))
        owner = positive_id(raw.get("upMid"))
        if owner != author.author_id:
            require((aid, bid, owner) in verified_exclusions, "PAGE_IDENTITY_INVALID")
            excluded.append(aid)
            continue
        items.append(
            Work(
                "bilibili",
                aid,
                author.author_id,
                text(raw.get("title")),
                timestamp(raw.get("pubdate")),
                "https://www.bilibili.com/video/" + bid,
                metadata_url(raw.get("pic")),
                "video",
            )
        )
    page = Page(items, str(number + 1) if more else None, more, tuple(excluded))
    page.validated(author)
    return page


class Bilibili:
    def __init__(self, transport):
        self.transport = transport
        self._verified_exclusions = set()

    def resolve_author(self, link):
        link = resolve_bilibili_link(link, self.transport.redirect)
        u = urlsplit(link)
        if u.hostname == "space.bilibili.com":
            aid = positive_id(u.path.strip("/").split("/")[0], "SOURCE_LINK_INVALID")
            user = envelope(self.transport.call(BL + "fetch_user_profile", {"uid": aid}))
            require(positive_id(user.get("mid")) == aid, "PAGE_IDENTITY_INVALID")
        else:
            match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10}|av[0-9]+)/?", u.path)
            require(match is not None, "SOURCE_LINK_INVALID")
            wid = match[1]
            if wid.startswith("BV"):
                video = envelope(self.transport.call(BL + "fetch_one_video", {"bv_id": wid}))
                require(video.get("bvid") == wid, "PAGE_IDENTITY_INVALID")
            else:
                video = envelope(self.transport.call(BL + "fetch_video_detail", {"aid": wid[2:]}))
                require(positive_id(video.get("aid")) == wid[2:], "PAGE_IDENTITY_INVALID")
            user = video.get("owner")
            require(isinstance(user, dict), "PAGE_IDENTITY_INVALID")
            aid = positive_id(user.get("mid"))
        return Author("bilibili", aid, text(user.get("name")) or aid, "https://space.bilibili.com/" + aid)

    def fetch_page(self, author, cursor):
        aid = positive_id(author.author_id)
        number = int(positive_id(cursor, "CURSOR_INVALID")) if cursor is not None else 1
        data = self.transport.call(BL + "fetch_user_post_videos_v2", {"uid": aid, "pn": number, "ps": 20})
        entries = envelope(data).get("archives")
        require(isinstance(entries, list))
        verified = set()
        for raw in entries:
            require(isinstance(raw, dict))
            owner = positive_id(raw.get("upMid"))
            if owner == aid:
                continue
            wid, bid = positive_id(raw.get("aid")), bvid(raw.get("bvid"))
            identity = (wid, bid, owner)
            scoped = (aid, *identity)
            if scoped not in self._verified_exclusions:
                video = envelope(self.transport.call(BL + "fetch_one_video", {"bv_id": bid}))
                require(video.get("bvid") == bid, "PAGE_IDENTITY_INVALID")
                require(positive_id(video.get("aid")) == wid, "PAGE_IDENTITY_INVALID")
                detail_owner, staff = video.get("owner"), video.get("staff")
                require(isinstance(detail_owner, dict), "PAGE_IDENTITY_INVALID")
                require(positive_id(detail_owner.get("mid")) == owner, "PAGE_IDENTITY_INVALID")
                require(isinstance(staff, list), "PAGE_IDENTITY_INVALID")
                require(
                    any(isinstance(person, dict) and str(person.get("mid")) == aid for person in staff),
                    "PAGE_IDENTITY_INVALID",
                )
                self._verified_exclusions.add(scoped)
            verified.add(identity)
        return parse_page(data, author, number, verified)

    def enrich_metadata(self, author, work):
        return work
