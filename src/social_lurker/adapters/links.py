import re
from urllib.parse import urlsplit

from ..errors import require
from .base import public_url


def source_link(value):
    urls = re.findall(r"https://[^\s<>]+", value)
    require(len(urls) == 1, "SOURCE_LINK_INVALID")
    link = urls[0].rstrip("。，；！）")
    require(public_url(link, ("douyin.com", "iesdouyin.com", "weixin.qq.com")), "SOURCE_LINK_INVALID")
    u = urlsplit(link)
    if u.hostname in {"weixin.qq.com", "www.weixin.qq.com"}:
        require(u.path.startswith("/sph/") and len(u.path) > 5, "SOURCE_LINK_INVALID")
        platform = "wechat_channels"
    else:
        require(
            u.hostname in {"douyin.com", "www.douyin.com", "v.douyin.com", "www.iesdouyin.com"},
            "SOURCE_LINK_INVALID",
        )
        require(u.path != "/", "SOURCE_LINK_INVALID")
        platform = "douyin"
    return platform, link
