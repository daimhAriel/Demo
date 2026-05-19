"""微信公众号文章抓取 — 通过 mptext.top API"""
import asyncio
import logging
from datetime import datetime
from typing import AsyncGenerator, Optional

import requests

from models import RawPage

logger = logging.getLogger(__name__)

BASE_URL = "https://down.mptext.top/api/public/v1"


# ── 单个 API 调用（同步，通过 asyncio.to_thread 异步化） ──

def _search_accounts(keyword: str, api_key: str, begin: int = 0, size: int = 5) -> list[dict]:
    """搜索公众号，返回 [{fakeid, nickname, alias, signature, round_head_img}, ...]"""
    resp = requests.get(
        f"{BASE_URL}/account",
        params={"keyword": keyword, "begin": begin, "size": min(size, 20)},
        headers={"X-Auth-Key": api_key},
        timeout=15,
    )
    if resp.status_code != 200:
        raise Exception(f"搜索公众号失败 HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"搜索公众号失败: {data.get('msg', str(data)[:200])}")
    return data.get("data", {}).get("accounts", [])


def _fetch_list(fakeid: str, api_key: str, begin: int = 0, size: int = 20) -> list[dict]:
    """获取文章列表 [{aid, title, link, digest, create_time, cover, author_name}, ...]"""
    resp = requests.get(
        f"{BASE_URL}/article",
        params={"fakeid": fakeid, "begin": begin, "size": min(size, 20)},
        headers={"X-Auth-Key": api_key},
        timeout=15,
    )
    if resp.status_code != 200:
        raise Exception(f"获取文章列表失败 HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"获取文章列表失败: {data.get('msg', str(data)[:200])}")
    return data.get("data", {}).get("articles", [])


def _download_article(url: str, fmt: str = "text") -> str:
    """下载单篇文章全文"""
    try:
        resp = requests.get(
            f"{BASE_URL}/download",
            params={"url": url, "format": fmt},
            timeout=30,
        )
        if resp.status_code != 200:
            return ""
        data = resp.json()
        if data.get("code") != 0:
            return ""
        return data.get("data", {}).get("content", "")
    except Exception:
        return ""


# ── 公开函数：搜索公众号（UI 用） ──

def search_accounts(keyword: str, api_key: str, begin: int = 0) -> list[dict]:
    """搜索公众号，直接返回账号列表"""
    return _search_accounts(keyword, api_key, begin)


# ── 公开函数：校验 API Key 是否有效（UI 用） ──

def check_api_key(api_key: str) -> dict:
    """验证 API Key 是否有效（随便搜个常见关键词，看返回）

    Returns:
        {"valid": bool, "message": str}
    """
    try:
        accounts = _search_accounts("36氪", api_key, 0, 1)
        if isinstance(accounts, list):
            return {"valid": True, "message": f"API Key 有效，共 {len(accounts)} 条结果"}
        return {"valid": False, "message": "返回数据格式异常"}
    except Exception as e:
        return {"valid": False, "message": str(e)}


# ── 公开异步生成器：分页获取文章（pipeline 用） ──

async def fetch_articles(
    fakeid: str,
    api_key: str,
    site_id: str,
    known_urls: Optional[set] = None,
    last_seen_url: Optional[str] = None,
    max_pages: int = 5,
    delay: float = 1.5,
) -> AsyncGenerator[RawPage, None]:
    """
    分页拉取公众号文章，配合锚点去重。
    - known_urls: 数据库中已有的 URL 集合（前置跳过）
    - last_seen_url: 上次抓的最新文章链接（锚点截断）
    - max_pages: 最大翻页数（每页 20 条）
    - delay: 翻页间隔秒数

    Yields RawPage（按时间降序，最新的在前）。
    """
    known = set(known_urls or [])
    seen_urls = set()
    if last_seen_url:
        seen_urls.add(last_seen_url)

    for page in range(max_pages):
        begin = page * 20
        articles = await asyncio.to_thread(_fetch_list, fakeid, api_key, begin, 20)
        if not articles:
            break

        for art in articles:
            link = art.get("link", "")
            if not link:
                continue

            # 锚点命中 → 停止整站爬取
            if link in seen_urls:
                logger.info(f"  [锚点截断] 命中: {link[:60]}")
                return

            # 已在数据库中 → 跳过但不停止（可能前面有更新的文章）
            if link in known:
                continue

            # 取全文
            content = await asyncio.to_thread(_download_article, link, "text")
            raw_text = content or art.get("digest", art.get("title", ""))

            # 时间处理
            create_ts = art.get("create_time", 0)
            if create_ts:
                time_snippet = datetime.fromtimestamp(create_ts).strftime("%Y-%m-%d")
            else:
                time_snippet = ""

            raw = RawPage(
                source_url=link,
                title=art.get("title", ""),
                raw_text=raw_text[:8000],   # 截取前 8000 避免过大
                html_snippet="",
                time_snippet=time_snippet,
                site_id=site_id,
            )
            seen_urls.add(link)
            yield raw

        # 翻到最后一页
        if len(articles) < 20:
            break

        await asyncio.sleep(delay)
