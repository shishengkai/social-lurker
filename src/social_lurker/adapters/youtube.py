"""YouTube videos and Shorts as one logical page, with run-local feed cursors."""

import json
import re
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

from ..errors import LurkerError, require
from ..transport import YT
from .base import Author, Page, Work, text
from .common import cursor_token, flag, iso_timestamp, metadata_url
from .links import source_link

CHANNEL = re.compile(r"UC[A-Za-z0-9_-]{22}")
VIDEO = re.compile(r"[A-Za-z0-9_-]{11}")


def channel_id(value):
    require(isinstance(value, str) and CHANNEL.fullmatch(value), "PAGE_IDENTITY_INVALID")
    return value


def video_id(value):
    require(isinstance(value, str) and VIDEO.fullmatch(value), "PAGE_IDENTITY_INVALID")
    return value


def thumbnail(data):
    if isinstance(data, dict):
        data = data.get("thumbnails")
    if isinstance(data, list):
        return next(
            (
                u
                for entry in reversed(data)
                if isinstance(entry, dict) and (u := metadata_url(entry.get("url")))
            ),
            None,
        )
    return None


def detail(data, expected):
    player = data.get("playerResponse")
    require(isinstance(player, dict))
    video = player.get("videoDetails")
    require(isinstance(video, dict))
    require(video_id(video.get("videoId")) == expected, "PAGE_IDENTITY_INVALID")
    aid = channel_id(video.get("channelId"))
    microformat = player.get("microformat", {})
    require(isinstance(microformat, dict))
    micro = microformat.get("playerMicroformatRenderer", {})
    require(isinstance(micro, dict))
    return Work(
        "youtube",
        expected,
        aid,
        text(video.get("title")),
        iso_timestamp(micro.get("publishDate")),
        "https://www.youtube.com/watch?v=" + expected,
        thumbnail(video.get("thumbnail")),
        "video",
    ), video


def listing_work(raw, aid, shorts=False):
    require(isinstance(raw, dict))
    wid = video_id(raw.get("video_id"))
    # Individual lists may omit ownership; the validated channel envelope scopes the whole page.
    if raw.get("channel_id") is not None:
        require(raw["channel_id"] == aid, "PAGE_IDENTITY_INVALID")
    value = raw.get("video_url" if shorts else "url")
    if value is not None:
        require(source_link(value)[0] == "youtube", "PAGE_IDENTITY_INVALID")
        u = urlsplit(value)
        declared = (
            u.path.rsplit("/", 1)[-1] if "/shorts/" in u.path else parse_qs(u.query).get("v", [None])[0]
        )
        require(declared == wid, "PAGE_IDENTITY_INVALID")
    return Work(
        "youtube",
        wid,
        aid,
        text(raw.get("title")),
        None,
        "https://www.youtube.com/watch?v=" + wid,
        metadata_url(raw.get("thumbnail")) or thumbnail(raw.get("thumbnails")),
        "video",
    )


class Youtube:
    def __init__(self, transport):
        self.transport = transport

    def video_detail(self, wid):
        return detail(
            self.transport.call(
                YT + "get_video_info",
                {
                    "video_id": wid,
                    "need_format": False,
                },
            ),
            wid,
        )

    def resolve_author(self, link):
        require(source_link(link)[0] == "youtube", "SOURCE_LINK_INVALID")
        u = urlsplit(link)
        if u.hostname == "youtu.be":
            wid = u.path.strip("/")
        elif u.path == "/watch":
            values = parse_qs(u.query).get("v", [])
            require(len(values) == 1, "SOURCE_LINK_INVALID")
            wid = values[0]
        elif re.fullmatch(r"/(shorts|live|embed)/[A-Za-z0-9_-]{11}/?", u.path):
            wid = u.path.strip("/").split("/")[-1]
        else:
            wid = None
        if wid is not None:
            require(VIDEO.fullmatch(wid), "SOURCE_LINK_INVALID")
            work, raw = self.video_detail(wid)
            aid, name = work.author_id, text(raw.get("author"))
        else:
            path = u.path.rstrip("/")
            match = re.fullmatch(
                r"/channel/(UC[A-Za-z0-9_-]{22})(?:/(?:videos|shorts|streams|featured))?", path
            )
            if match:
                aid = match[1]
            else:
                require(
                    re.fullmatch(
                        r"/(?:@[^/?#\s]+|(?:c|user)/[^/?#\s]+)(?:/(?:videos|shorts|streams|featured))?", path
                    ),
                    "SOURCE_LINK_INVALID",
                )
                resolved = self.transport.call(YT + "get_channel_id", {"channel_url": link})
                aid = channel_id(resolved.get("channel_id"))
            profile = self.transport.call(
                YT + "get_channel_description",
                {
                    "channel_id": aid,
                    "need_format": True,
                },
            )
            require(profile.get("channel_id") == aid, "PAGE_IDENTITY_INVALID")
            name = text(profile.get("title"))
        return Author("youtube", aid, name or aid, "https://www.youtube.com/channel/" + aid)

    def fetch_page(self, author, cursor):
        aid = channel_id(author.author_id)
        state = {"author": aid, "videos": "", "shorts": ""}
        if cursor is not None:
            try:
                state = json.loads(cursor)
            except (ValueError, TypeError):
                raise LurkerError("CURSOR_INVALID") from None
            require(
                isinstance(state, dict)
                and set(state) == {"author", "videos", "shorts"}
                and state["author"] == aid,
                "CURSOR_INVALID",
            )
            for feed in ("videos", "shorts"):
                require(
                    state[feed] is False or isinstance(state[feed], str) and bool(state[feed]),
                    "CURSOR_INVALID",
                )
        items = []
        for feed in ("videos", "shorts"):
            token = state[feed]
            if token is False:
                continue
            params = {"channel_id": aid, "need_format": True}
            if token:
                params["continuation_token"] = cursor_token(token)
            data = self.transport.call(YT + "get_channel_" + feed, params)
            entries = data.get(feed)
            require(isinstance(entries, list))
            if feed == "videos":
                channel = data.get("channel")
                if channel is not None or not token:
                    require(isinstance(channel, dict) and channel.get("id") == aid, "PAGE_IDENTITY_INVALID")
                # YouTube omits channel metadata on continuation pages; cursor binds it to this author.
                more = data.get("continuation_token") is not None and data.get("continuation_token") != ""
            else:
                require(data.get("channel_id") == aid, "PAGE_IDENTITY_INVALID")
                more = flag(data.get("has_more"))
                require(type(data.get("total_count")) is int and data["total_count"] == len(entries))
            next_token = cursor_token(data.get("continuation_token")) if more else False
            require(not more or bool(entries), "CURSOR_INVALID")
            items.extend(listing_work(raw, aid, feed == "shorts") for raw in entries)
            state[feed] = next_token
        more = any(state[feed] is not False for feed in ("videos", "shorts"))
        page = Page(items, json.dumps(state, separators=(",", ":")) if more else None, more)
        page.validated(author)
        return page

    def enrich_metadata(self, author, work):
        if work.published_at is not None and work.title is not None:
            return work
        value, _ = self.video_detail(work.work_id)
        value.validate(author)
        return replace(
            work,
            title=value.title or work.title,
            published_at=value.published_at or work.published_at,
            cover_url=value.cover_url or work.cover_url,
        )
