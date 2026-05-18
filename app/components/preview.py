"""预览解析结果 — 用当前选择器实时抓取并展示样例"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.crawler import CrawlerManager
from models import SiteConfig
from bs4 import BeautifulSoup


async def preview_parsing(url: str, config: SiteConfig) -> list[dict]:
    """用给定的配置抓取页面并解析前 15 条结果"""
    crawler = CrawlerManager()
    try:
        html = await crawler.fetch_page(url, config)
    except Exception as e:
        return [{"error": str(e)}]
    finally:
        await crawler.close()

    items = crawler.parse_list_page(html, config)
    return items[:15]


async def preview_with_report(url: str, config: SiteConfig) -> dict:
    """抓取并返回 {items, report}，report 包含各选择器的命中数"""
    crawler = CrawlerManager()
    try:
        html = await crawler.fetch_page(url, config)
    except Exception as e:
        return {"items": [], "report": {"error": str(e)}, "html": ""}
    finally:
        await crawler.close()

    soup = BeautifulSoup(html, "lxml")
    base = soup.select_one(config.section_selector) if config.section_selector else soup
    container = base.select_one(config.list_selector) if config.list_selector else base

    list_hits = len(container) if config.list_selector else 1
    item_elements = container.select(config.item_selector) if config.item_selector else []
    item_count = len(item_elements)
    title_hits = sum(1 for el in item_elements if el.select_one(config.title_selector)) if config.title_selector else item_count
    link_hits = 0
    if config.link_selector:
        link_hits = sum(1 for el in item_elements if el.select_one(config.link_selector))
    else:
        link_hits = sum(1 for el in item_elements if el.name == "a" or el.find("a"))
    time_hits = sum(1 for el in item_elements if config.time_selector and el.select_one(config.time_selector)) if config.time_selector else -1

    items = crawler.parse_list_page(html, config)

    report = {
        "list_selector": {"selector": config.list_selector or "(无)", "hits": list_hits, "status": "ok" if list_hits > 0 else "warn"},
        "item_selector": {"selector": config.item_selector or "(无)", "hits": item_count, "status": "ok" if item_count > 0 else "error"},
        "title_selector": {"selector": config.title_selector or "(无，使用条目文本)", "hits": title_hits, "status": "ok"},
        "link_selector": {"selector": config.link_selector or "(无，使用条目自身)", "hits": link_hits, "status": "ok" if link_hits > 0 else "warn"},
    }
    if config.time_selector:
        report["time_selector"] = {"selector": config.time_selector, "hits": time_hits, "status": "ok" if time_hits > 0 else "warn"}

    return {"items": items[:15], "report": report}
