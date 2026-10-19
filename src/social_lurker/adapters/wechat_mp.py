"""WeChat official-account article metadata; never retain article bodies."""

import re
from urllib.parse import parse_qs, urlencode, urlsplit

from ..errors import require
from ..transport import MP
from .base import Author, Page, Work, text, timestamp
from .common import cursor_token, flag, metadata_url, positive_id
from .links import source_link

USERNAME = re.compile(r"[0-9A-Za-z][-_0-9A-Za-z]{1,63}(?:@[0-9A-Za-z]{1,16})?")


def username(value):
    require(isinstance(value, str) and USERNAME.fullmatch(value), "PAGE_IDENTITY_INVALID")
    return value


def article_url(value, mid=None, idx=None):
    value = metadata_url(value)
    require(value is not None and source_link(value)[0] == "wechat_mp", "PAGE_IDENTITY_INVALID")
    params = parse_qs(urlsplit(value).query)
    for key, expected in (("mid", mid), ("idx", idx)):
        if expected is not None and key in params:
            require(params[key] == [expected], "PAGE_IDENTITY_INVALID")
    return value


def parse_page(data, author):
    require(isinstance(data, dict))
    require(username(data.get("biz_username")) == author.author_id, "PAGE_IDENTITY_INVALID")
    articles = data.get("articles")
    require(isinstance(articles, list) and type(data.get("count")) is int and data["count"] == len(articles))
    more = not flag(data.get("is_end"))
    token = cursor_token(data.get("next_offset")) if more else None
    items = []
    for raw in articles:
        require(isinstance(raw, dict))
        mid, idx = positive_id(raw.get("app_msg_id")), positive_id(raw.get("idx"))
        url = article_url(raw.get("url"), mid, idx)
        if author.profile_url:
            expected = parse_qs(urlsplit(author.profile_url).query).get("__biz")
            observed = parse_qs(urlsplit(url).query).get("__biz")
            if expected is not None and observed is not None:
                require(observed == expected, "PAGE_IDENTITY_INVALID")
        # A batch may contain multiple articles; app_msg_id alone also collides across accounts.
        items.append(
            Work(
                "wechat_mp",
                f"{author.author_id}:{mid}:{idx}",
                author.author_id,
                text(raw.get("title")),
                timestamp(raw.get("create_time")),
                url,
                metadata_url(raw.get("cover")),
                "text",
            )
        )
    page = Page(items, token, more)
    page.validated(author)
    return page


class WechatMP:
    def __init__(self, transport):
        self.transport = transport

    def resolve_author(self, link):
        require(source_link(link)[0] == "wechat_mp", "SOURCE_LINK_INVALID")
        data = self.transport.call(MP + "fetch_article_detail_h5", {"url": link, "raw": False})
        content = data.get("content")
        require(isinstance(content, dict))
        aid = username(content.get("user_name"))
        for key in ("mid", "idx"):
            requested = parse_qs(urlsplit(link).query).get(key)
            if requested is not None:
                require(requested == [positive_id(content.get(key))], "PAGE_IDENTITY_INVALID")
        profile_url = None
        canonical = content.get("link")
        if canonical:
            canonical = article_url(
                canonical, positive_id(content.get("mid")), positive_id(content.get("idx"))
            )
            biz = parse_qs(urlsplit(canonical).query).get("__biz")
            requested_biz = parse_qs(urlsplit(link).query).get("__biz")
            if requested_biz is not None:
                require(biz == requested_biz, "PAGE_IDENTITY_INVALID")
            if biz:
                require(len(biz) == 1, "PAGE_IDENTITY_INVALID")
                profile_url = "https://mp.weixin.qq.com/mp/profile_ext?" + urlencode(
                    {"action": "home", "__biz": biz[0]}
                )
        return Author("wechat_mp", aid, text(content.get("nick_name")) or aid, profile_url)

    def fetch_page(self, author, cursor):
        username(author.author_id)
        return parse_page(
            self.transport.call(
                MP + "fetch_account_articles",
                {
                    "username": author.author_id,
                    "page_size": 20,
                    "offset": cursor_token(cursor) if cursor is not None else "",
                    "raw": False,
                },
            ),
            author,
        )

    def enrich_metadata(self, author, work):
        # List metadata is sufficient; missing fields stay explicit instead of fetching article text.
        return work
