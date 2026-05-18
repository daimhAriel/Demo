"""RSS/Atom 自动发现 — 当 HTML 解析失败时，用 feed 兜底抓取"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import feedparser
import requests
from bs4 import BeautifulSoup


COMMON_FEED_PATHS = [
    "/feed/",
    "/feed",
    "/rss/",
    "/rss",
    "/rss.xml",
    "/feed.xml",
    "/atom.xml",
    "/news/feed/",
    "/news/rss/",
    "/news/rss.xml",
    "/news/atom.xml",
    "/blog/feed/",
    "/blog/rss/",
    "/en/feed/",
    "/cn/feed/",
    "/media-center/feed/",
    "/media-center/rss/",
    "/press/feed/",
    "/press/rss/",
    "/press-releases/feed/",
]


def _fetch(url: str, timeout: int = 10) -> str | None:
    """抓取 URL 内容"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None


def discover_feed_urls(page_url: str) -> list[str]:
    """
    从页面中查找 feed URL。
    1. 扫描 <link> 标签
    2. 尝试常见路径
    """
    found: list[str] = []
    html = _fetch(page_url)
    if html:
        soup = BeautifulSoup(html, "lxml")
        for link in soup.find_all("link", type=re.compile(r"application/(rss|atom)\+xml", re.I)):
            href = link.get("href", "")
            if href:
                if href.startswith("/"):
                    from urllib.parse import urlparse
                    parsed = urlparse(page_url)
                    href = f"{parsed.scheme}://{parsed.netloc}{href}"
                found.append(href)

        # 也查 a 标签中的 feed 链接
        for a in soup.find_all("a", href=re.compile(r"(feed|rss|atom)", re.I)):
            href = a.get("href", "")
            if href and href not in found:
                if href.startswith("/"):
                    from urllib.parse import urlparse
                    parsed = urlparse(page_url)
                    href = f"{parsed.scheme}://{parsed.netloc}{href}"
                found.append(href)

    # 尝试常见路径
    from urllib.parse import urlparse
    parsed = urlparse(page_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    for path in COMMON_FEED_PATHS:
        url = base + path
        if url not in found:
            html2 = _fetch(url, timeout=5)
            if html2 and ("<rss" in html2.lower() or "<feed" in html2.lower() or "<rdf" in html2.lower()):
                found.append(url)

    return list(dict.fromkeys(found))  # 去重保序


def parse_feed(feed_url: str) -> dict | None:
    """
    解析 feed，返回：
    {
        "feed_url": "...",
        "feed_title": "...",
        "items": [{title, link, published, summary}, ...],
        "item_count": N,
    }
    解析失败返回 None。
    """
    try:
        f = feedparser.parse(feed_url)
    except Exception:
        return None

    if f.bozo and not f.entries:
        return None

    feed_title = f.feed.get("title", "") if hasattr(f, "feed") else ""
    items = []
    for entry in f.entries[:50]:
        items.append({
            "title": entry.get("title", ""),
            "link": entry.get("link", ""),
            "published": entry.get("published", entry.get("updated", "")),
            "summary": entry.get("summary", "")[:200] if entry.get("summary") else "",
        })

    return {
        "feed_url": feed_url,
        "feed_title": feed_title,
        "items": items,
        "item_count": len(items),
    }


async def discover_and_parse(url: str) -> dict | None:
    """
    一站式：从 URL 发现 feed → 解析 → 返回结果。
    若全失败返回 None。
    """
    feed_urls = discover_feed_urls(url)
    for feed_url in feed_urls:
        result = parse_feed(feed_url)
        if result and result["item_count"] > 0:
            return result
    return None
