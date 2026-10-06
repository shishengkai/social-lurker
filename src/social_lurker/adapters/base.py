import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from urllib.parse import urlsplit

from ..errors import require

PLATFORMS = {"douyin", "wechat_channels"}


def identity(value):
    require(
        isinstance(value, str) and 0 < len(value) <= 1024 and not re.search(r"[\x00-\x20\x7f]", value),
        "PAGE_IDENTITY_INVALID",
    )
    return value


def public_url(value, domains=None):
    if not isinstance(value, str) or len(value) > 16384 or any(ord(c) < 33 for c in value):
        return None
    try:
        u = urlsplit(value)
        if u.scheme != "https" or not u.hostname or u.username or u.password or u.port not in (None, 443):
            return None
        if domains and not any(u.hostname == d or u.hostname.endswith("." + d) for d in domains):
            return None
        # Signed/token URLs are not public metadata in this CLI.
        if re.search(r"(?i)(?:^|[?&])(token|key|signature|sign|auth|cookie|x-amz-[^=]+)=", u.query):
            return None
        return value
    except ValueError:
        return None


def text(value):
    return value if isinstance(value, str) and value else None


def timestamp(value):
    if type(value) is not int or not 0 < value < 4102444800:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Author:
    platform: str
    author_id: str
    name: str
    profile_url: str | None = None

    def validate(self):
        require(self.platform in PLATFORMS, "PLATFORM_UNSUPPORTED")
        identity(self.author_id)
        require(isinstance(self.name, str) and bool(self.name), "PAGE_IDENTITY_INVALID")


@dataclass(frozen=True)
class Work:
    platform: str
    work_id: str
    author_id: str
    title: str | None = None
    published_at: str | None = None
    url: str | None = None
    cover_url: str | None = None
    kind: str = "unknown"

    def validate(self, author):
        identity(self.work_id)
        identity(self.author_id)
        require(
            self.platform == author.platform and self.author_id == author.author_id, "PAGE_IDENTITY_INVALID"
        )
        require(self.kind in {"video", "image", "text", "unknown"})
        for value in (self.title, self.published_at, self.url, self.cover_url):
            require(value is None or isinstance(value, str) and bool(value))
        for value in (self.url, self.cover_url):
            require(value is None or public_url(value) is not None)
        if self.published_at is not None:
            try:
                parsed = datetime.fromisoformat(self.published_at.replace("Z", "+00:00"))
                require(parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0)
            except ValueError:
                require(False)


@dataclass(frozen=True)
class Page:
    items: list[Work]
    next_cursor: str | None = None
    has_more: bool = False
    # Providers can prove a co-created work belongs to someone else. It cannot authorize continuation.
    excluded_ids: tuple[str, ...] = ()

    def validated(self, author):
        require(type(self.has_more) is bool and isinstance(self.items, list))
        require(
            not self.has_more or (isinstance(self.next_cursor, str) and bool(self.next_cursor)),
            "CURSOR_INVALID",
        )
        require(
            self.next_cursor is None
            or (isinstance(self.next_cursor, str) and len(self.next_cursor.encode()) <= 16384),
            "CURSOR_INVALID",
        )
        unique = {}
        for item in self.items:
            require(isinstance(item, Work))
            item.validate(author)
            require(item.work_id not in self.excluded_ids, "PAGE_IDENTITY_INVALID")
            unique.setdefault(item.work_id, item)
        for value in self.excluded_ids:
            identity(value)
        require(not self.has_more or bool(unique or self.excluded_ids), "CURSOR_INVALID")
        return list(unique.values())


class Adapter(Protocol):
    def resolve_author(self, link: str) -> Author: ...
    def fetch_page(self, author: Author, cursor: str | None) -> Page: ...
    def enrich_metadata(self, author: Author, work: Work) -> Work: ...
