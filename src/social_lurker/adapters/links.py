import re
from html import unescape
from urllib.parse import parse_qs, urljoin, urlsplit

from ..errors import require
from .base import public_url


def source_link(value):
    urls = re.findall(r"https://[^\s<>]+", unescape(value))
    require(len(urls) == 1, "SOURCE_LINK_INVALID")
    link = urls[0].rstrip("。，；！）")
    require(
        public_url(
            link,
            (
                "douyin.com",
                "iesdouyin.com",
                "weixin.qq.com",
                "bilibili.com",
                "b23.tv",
                "youtube.com",
                "youtu.be",
            ),
        ),
        "SOURCE_LINK_INVALID",
    )
    u = urlsplit(link)
    if u.hostname == "mp.weixin.qq.com":
        require(
            re.fullmatch(r"/s/[^/?#]+/?", u.path)
            or u.path == "/s"
            and len(parse_qs(u.query).get("__biz", [])) == 1,
            "SOURCE_LINK_INVALID",
        )
        platform = "wechat_mp"
    elif u.hostname in {"space.bilibili.com", "www.bilibili.com", "bilibili.com", "m.bilibili.com", "b23.tv"}:
        platform = "bilibili"
    elif u.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        platform = "youtube"
    elif u.hostname in {"weixin.qq.com", "www.weixin.qq.com"}:
        require(u.path.startswith("/sph/") and len(u.path) > 5, "SOURCE_LINK_INVALID")
        platform = "wechat_channels"
    else:
        require(
            u.hostname
            in {"douyin.com", "www.douyin.com", "v.douyin.com", "iesdouyin.com", "www.iesdouyin.com"},
            "SOURCE_LINK_INVALID",
        )
        require(u.path != "/", "SOURCE_LINK_INVALID")
        platform = "douyin"
    return platform, link


def resolve_bilibili_link(link, redirect):
    platform, current = source_link(link)
    require(platform == "bilibili", "SOURCE_LINK_INVALID")
    seen = set()
    for hops in range(6):
        u = urlsplit(current)
        if u.hostname != "b23.tv":
            require(
                u.hostname == "space.bilibili.com"
                and re.fullmatch(r"/[1-9][0-9]*(?:/[^?#]*)?", u.path)
                or u.hostname in {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}
                and re.fullmatch(r"/video/(?:BV[0-9A-Za-z]{10}|av[1-9][0-9]*)/?", u.path),
                "SOURCE_LINK_INVALID",
            )
            return current
        require(
            hops < 5 and current not in seen and re.fullmatch(r"/[A-Za-z0-9_-]+/?", u.path),
            "SOURCE_LINK_INVALID",
        )
        seen.add(current)
        location = redirect(current)
        require(
            isinstance(location, str)
            and bool(location)
            and len(location) <= 16384
            and not any(ord(c) < 33 for c in location),
            "SOURCE_LINK_INVALID",
        )
        platform, current = source_link(urljoin(current, location))
        require(platform == "bilibili", "SOURCE_LINK_INVALID")
    raise AssertionError("unreachable")


def douyin_identity(link):
    """Classify verified paths, not page HTML or guessed author names."""
    u = urlsplit(link)
    if u.hostname == "v.douyin.com":
        return None
    prefix = "/share/" if u.hostname in {"iesdouyin.com", "www.iesdouyin.com"} else "/"
    user = re.fullmatch(re.escape(prefix) + r"user/([A-Za-z0-9_-]{1,1024})/?", u.path)
    if user:
        return "user", user[1]
    video = re.fullmatch(re.escape(prefix) + r"(?:video|note)/([0-9]{1,1024})/?", u.path)
    if video:
        return "work", video[1]
    return None


def resolve_douyin_link(link, redirect):
    platform, current = source_link(link)
    require(platform == "douyin", "SOURCE_LINK_INVALID")
    seen = set()
    for hops in range(6):
        result = douyin_identity(current)
        if result:
            return result
        require(
            hops < 5
            and current not in seen
            and urlsplit(current).hostname == "v.douyin.com"
            and re.fullmatch(r"/[A-Za-z0-9_-]+/?", urlsplit(current).path),
            "SOURCE_LINK_INVALID",
        )
        seen.add(current)
        location = redirect(current)
        require(
            isinstance(location, str)
            and bool(location)
            and len(location) <= 16384
            and not any(ord(c) < 33 for c in location),
            "SOURCE_LINK_INVALID",
        )
        platform, current = source_link(urljoin(current, location))
        require(platform == "douyin", "SOURCE_LINK_INVALID")
    raise AssertionError("unreachable")
