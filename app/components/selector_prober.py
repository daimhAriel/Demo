"""选择器一键探测 — 输入 URL，自动分析 DOM 结构推荐选择器 + 翻页检测

核心思想：不盲猜选择器字符串，而是「猜 → 试提取 → 看效果 → 选最优」。
"""
import sys, re, json
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bs4 import BeautifulSoup
from .rss_discovery import discover_and_parse


# ── 垃圾标题检测 ──
JUNK_PATTERNS = re.compile(
    r'(阅读全文|>>|more|详情|详细|查看|点击|Read\s*More|→|»|\|)',
    re.IGNORECASE,
)


def _is_junk_title(title: str) -> bool:
    """检测标题是否为垃圾文本"""
    t = title.strip()
    if not t or len(t) < 6:
        return True
    return bool(JUNK_PATTERNS.search(t))


# ── 抓取 ──

def _fetch_via_requests(url: str) -> str:
    """同步 requests 抓取（仅用于无 Cloudflare 的站点）"""
    import requests
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def _try_dynamic_subprocess(url: str) -> str:
    """
    尝试在子进程中用 Playwright 抓取（应对 Cloudflare 等）。
    若失败（如 Windows 多实例冲突），返回空字符串而非抛出异常。
    """
    tmpfile = Path(__file__).parent / "_probe_subprocess.py"
    code = (
        'import asyncio, sys\n'
        'sys.stdout.reconfigure(encoding="utf-8")\n'
        'async def run():\n'
        '    from playwright.async_api import async_playwright\n'
        '    p = await async_playwright().start()\n'
        '    b = await p.chromium.launch(headless=True, args=["--no-sandbox"])\n'
        '    ctx = await b.new_context()\n'
        '    pg = await ctx.new_page()\n'
        f'    await pg.goto({url!r}, wait_until="domcontentloaded", timeout=30000)\n'
        '    html = await pg.content()\n'
        '    await ctx.close()\n'
        '    await b.close()\n'
        '    await p.stop()\n'
        '    sys.stdout.write(str(len(html)) + "\\n")\n'
        '    sys.stdout.write(html)\n'
        'asyncio.run(run())\n'
    )
    try:
        tmpfile.write_text(code, encoding="utf-8")
        import subprocess
        result = subprocess.run(
            [sys.executable, str(tmpfile)],
            capture_output=True, timeout=60, text=True,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            return ""
        lines = result.stdout.split("\n", 1)
        if not lines[0].strip():
            return ""
        length = int(lines[0].strip())
        html = lines[1] if len(lines) > 1 else ""
        if len(html) != length:
            return ""
        return html
    except Exception:
        return ""
    finally:
        if tmpfile.exists():
            tmpfile.unlink(missing_ok=True)


def _get_selector_for_tag(tag) -> str:
    """为任意 Tag 生成 CSS 选择器（处理 ID 以数字开头等边界情况）"""
    if tag.get("id"):
        id_val = tag["id"]
        # CSS IDs cannot start with a digit; fall back to attribute selector
        if id_val and id_val[0].isdigit():
            return f'{tag.name}[id="{id_val}"]'
        return f"#{id_val}"
    if tag.get("class"):
        classes = [c for c in tag["class"] if c and not c.startswith(":")]
        if classes:
            return f"{tag.name}.{'.'.join(classes)}"
    return tag.name


# ── 实际提取测试 ──

def _test_extraction(html: str, list_sel: str, item_sel: str, title_sel: str, link_sel: str = "") -> list[dict]:
    """
    用给定的选择器组合实际提取内容，返回 [{title, link}, ...]。
    这是最核心的函数 — 不猜，直接试。
    """
    soup = BeautifulSoup(html, "lxml")
    base = soup.select_one(list_sel) if list_sel else soup
    if not base:
        return []

    items = base.select(item_sel) if item_sel else []
    results = []
    for el in items[:30]:
        # 提取标题
        if title_sel and title_sel != item_sel:
            title_el = el.select_one(title_sel)
        else:
            title_el = el

        if not title_el:
            title_el = el
        title = title_el.get_text(strip=True) if title_el else ""

        # 提取链接
        link = ""
        if link_sel and link_sel != item_sel:
            link_el = el.select_one(link_sel)
        elif el.name == "a":
            link_el = el
        else:
            link_el = el.find("a")

        if link_el and link_el.name == "a":
            link = link_el.get("href", "")

        if title:
            results.append({"title": title, "link": link})

    return results


def _find_best_extraction(html: str, container_sel: str) -> dict:
    """
    给定容器选择器，尝试多种 item/title 选择器策略，
    返回最佳的组合 + 实际提取的样例数据。
    """
    soup = BeautifulSoup(html, "lxml")
    container = soup.select_one(container_sel) if container_sel else soup
    if not container:
        return {"item_sel": "", "title_sel": "", "link_sel": "", "items": [], "quality": 0, "total_items": 0}

    strategies = []
    all_a = container.find_all("a", href=True)

    # 策略 1: 容器内的直接 <a> 子元素（DJI 模式：条目本身就是 <a>）
    direct_a = [c for c in container.children if hasattr(c, 'name') and c.name == 'a' and c.get('href', '').strip()]
    if 3 <= len(direct_a) <= 50:
        strategies.append({"item_sel": "a", "title_sel": "", "link_sel": "a", "desc": "直接<a>"})

    # 策略 2: 容器内所有 <a>
    if 3 <= len(all_a) <= 100:
        strategies.append({"item_sel": "a", "title_sel": "", "link_sel": "a", "desc": "所有<a>"})

    # 策略 3: <li> 直接子元素
    lis = [c for c in container.children if hasattr(c, 'name') and c.name == 'li']
    if 3 <= len(lis) <= 50:
        strategies.append({"item_sel": "li", "title_sel": "a", "link_sel": "a", "desc": "<li>子元素"})
        strategies.append({"item_sel": "li", "title_sel": "h2, h3, h4, a[href]", "link_sel": "a", "desc": "<li>+标题"})

    # 策略 4: 直接子元素 div/article/section
    for tagname in ["div", "article", "section", "main"]:
        children = [c for c in container.children if hasattr(c, 'name') and c.name == tagname]
        if 2 <= len(children) <= 40:
            strategies.append({"item_sel": tagname, "title_sel": "a", "link_sel": "a", "desc": f"子{tagname}"})
            strategies.append({"item_sel": tagname, "title_sel": "h2, h3, h4, a[href]", "link_sel": "a", "desc": f"子{tagname}+标题"})

    # 策略 5: href 路径模式检测（适合 DJI/Ehang 这类结构化站点）
    if len(all_a) >= 5:
        path_patterns = Counter()
        for a in all_a:
            h = a.get("href", "")
            m = re.search(
                r'/(?:news|article|announcement|media|press|release|blog|story'
                r'|item|detail|view|show|cnt|d|p|n|post|entry|new|info)',
                h,
            )
            if m:
                path_patterns[m.group(0)] += 1
        for pattern, count in path_patterns.most_common(3):
            if count >= 3:
                sel = f'a[href*="{pattern}"]'
                strategies.append({"item_sel": sel, "title_sel": "", "link_sel": sel, "desc": f"路径:{pattern}"})

    # 策略 6: 日期路径模式 (/YYYY/MM/DD/ 或 /YYYY-MM-DD/)
    if len(all_a) >= 3:
        date_a = [a for a in all_a if re.search(r'/\d{4}[/-]\d{2}[/-]\d{2}/', a.get("href", ""))]
        if len(date_a) >= 3:
            strategies.append({"item_sel": "a", "title_sel": "", "link_sel": "a", "desc": "日期路径"})

    # 测试所有策略，找出最优
    best = {"item_sel": "", "title_sel": "", "link_sel": "", "items": [], "quality": -1, "total_items": 0}

    seen_strategies = set()
    for s in strategies:
        key = f"{s['item_sel']}|{s['title_sel']}"
        if key in seen_strategies:
            continue
        seen_strategies.add(key)

        items = _test_extraction(html, container_sel, s["item_sel"], s["title_sel"], s.get("link_sel", ""))
        if not items:
            continue

        meaningful = sum(1 for it in items if not _is_junk_title(it["title"]))
        # 评分：有意义的标题越多越好，至少 3 条才算有效
        score = meaningful if meaningful >= 3 else 0

        if score > best["quality"] or (score == best["quality"] and meaningful > best.get("meaningful", 0)):
            best = {
                "item_sel": s["item_sel"],
                "title_sel": s.get("title_sel", ""),
                "link_sel": s.get("link_sel", ""),
                "items": items[:15],
                "quality": score,
                "total_items": len(items),
                "meaningful": meaningful,
                "desc": s.get("desc", ""),
            }

    return best


# ── 翻页检测 ──

def _detect_pagination(soup: BeautifulSoup, url: str) -> dict | None:
    """检测翻页方式"""
    result: dict = {"type": None, "selector": None, "hint": ""}
    parsed = urlparse(url)

    # 1. 查找翻页按钮/链接
    pagination_texts = ["next", "下一页", "后页", "load more", "加载更多", "»", "›", "last"]
    for a_tag in soup.find_all("a", href=True):
        text = a_tag.get_text(strip=True).lower()
        if any(pt in text for pt in pagination_texts):
            result["type"] = "click"
            result["selector"] = _get_selector_for_tag(a_tag)
            result["hint"] = f"发现「{a_tag.get_text(strip=True)}」链接 → click 翻页"
            return result

    # 2. 翻页数字列表检测
    page_links = []
    for a_tag in soup.find_all("a", href=True):
        text = a_tag.get_text(strip=True)
        if text.isdigit() and 2 <= int(text) <= 999:
            page_links.append(a_tag)
    if len(page_links) >= 3:
        parent = page_links[0].parent
        max_page = max(int(a.get_text(strip=True)) for a in page_links if a.get_text(strip=True).isdigit())
        result["type"] = "query"
        result["selector"] = _get_selector_for_tag(parent) if parent else None
        result["hint"] = f"发现翻页数字（共约{max_page}页）→ query 翻页"
        return result

    # 3. URL 已有 page 参数
    has_page_param = bool(parse_qs(parsed.query).get("page"))
    has_page_path = bool(re.search(r"/page/\d+", parsed.path))
    if has_page_param or has_page_path:
        result["type"] = "query"
        result["hint"] = "URL 包含页码参数 → query 翻页"
        return result

    # 4. 未知
    result["type"] = "unknown"
    result["hint"] = "未检测到明显翻页元素，手动配置"
    return result


# ── 公开 API ──

async def probe_selectors(url: str, mode: str = "static") -> dict:
    """
    访问页面，自动分析常见列表结构，返回候选选择器 + 翻页建议。

    与旧版本的关键区别：
    - 每个候选选择器都会用多种 item/title 策略**实际试提取**
    - 返回提取到的样例标题，而不是只有容器选择器
    - 按内容质量排序

    - static: 用 requests 同步抓取（无需浏览器）
    - dynamic: 先试 requests（快）；失败则子进程 Playwright；再失败则提示 Cloudflare
    """
    html = None
    error_info = None

    # 先试 requests（无 Cloudflare 的站点都能成功）
    try:
        html = _fetch_via_requests(url)
    except Exception as e:
        error_info = f"requests 失败: {type(e).__name__}"
        # dynamic 模式才尝试子进程 Playwright
        if mode == "dynamic":
            html = _try_dynamic_subprocess(url)
            if html:
                error_info = None

    if html is None:
        msg = error_info or "无法获取页面内容"
        if "requests" in (error_info or ""):
            msg += (
                "\n\n该网站可能使用了 Cloudflare/反爬保护，需要浏览器渲染。"
                "\n建议：将爬取模式设为 dynamic，然后保存站点配置后直接试爬。"
            )
        return {"url": url, "error": msg, "candidates": [], "pagination": None}

    # 保存原始 HTML 供 LLM 使用
    result: dict = {"url": url, "_raw_html": html, "candidates": [], "pagination": None}

    soup = BeautifulSoup(html, "lxml")

    # ── 翻页检测 ──
    result["pagination"] = _detect_pagination(soup, url)

    # ── 候选容器检测 ──
    seen = set()
    candidates = []

    # 候选 1: 数据属性（data-*）
    for attr_name in ("list", "items", "news", "article", "posts", "grid"):
        for el in soup.find_all(attrs={f"data-{attr_name}": True}):
            sel = _get_selector_for_tag(el)
            link_count = len(el.find_all("a", href=True))
            if link_count >= 3 and sel not in seen:
                seen.add(sel)
                candidates.append({
                    "selector": sel,
                    "type": "data_attribute",
                    "item_count": link_count,
                    "sample_title": "",
                })

    # 候选 2: 常见列表标签 ul, ol
    for lst_tag in ["ul", "ol"]:
        for el in soup.find_all(lst_tag):
            items = el.find_all("li", recursive=False)
            if len(items) >= 3:
                sel = _get_selector_for_tag(el)
                if sel not in seen:
                    seen.add(sel)
                    candidates.append({
                        "selector": sel,
                        "type": f"{lst_tag}/li",
                        "item_count": len(items),
                        "sample_title": (items[0].get_text(strip=True)[:60] if items else ""),
                    })

    # 候选 3: 链接密度高的块级容器
    for tag in ["div", "section", "main", "article", "aside"]:
        for el in soup.find_all(tag):
            sel = _get_selector_for_tag(el)
            if sel in seen:
                continue
            links = el.find_all("a", href=True)
            if len(links) < 3:
                continue
            total_len = sum(len(a.get_text(strip=True)) for a in links)
            # 过滤：跳过导航栏、页脚等 (短的文本块)
            if total_len < 10:
                continue
            seen.add(sel)
            candidates.append({
                "selector": sel,
                "type": "link_density",
                "item_count": len(links),
                "sample_title": (links[0].get_text(strip=True)[:60] if links else ""),
            })

    # 候选 4: table 列表
    for el in soup.find_all("table"):
        rows = el.find_all("tr")
        if len(rows) >= 3:
            sel = _get_selector_for_tag(el)
            if sel not in seen:
                seen.add(sel)
                candidates.append({
                    "selector": sel,
                    "type": "table",
                    "item_count": len(rows),
                    "sample_title": "",
                })

    # ── 对每个候选容器，实际试提取 ──
    enriched = []
    for cand in candidates:
        extraction = _find_best_extraction(html, cand["selector"])
        cand["best_item_sel"] = extraction["item_sel"]
        cand["best_title_sel"] = extraction["title_sel"]
        cand["best_link_sel"] = extraction["link_sel"]
        cand["samples"] = extraction["items"]
        cand["quality"] = extraction["quality"]
        cand["total_extracted"] = extraction["total_items"]
        enriched.append(cand)

    # 按提取质量降序排列：quality * meaningful items / total items
    def _score_candidate(c):
        q = c.get("quality", 0)
        t = c.get("total_extracted", 0)
        return q * (q / max(t, 1))  # 质量越高越好，质量占比也重要


    # ── RSS 自动发现兜底（所有启发式候选质量不佳时） ──
    best_quality = max((c.get("quality", 0) for c in enriched), default=0)
    if best_quality < 3:
        try:
            rss_result = await discover_and_parse(url)
            if rss_result and rss_result.get("item_count", 0) > 0:
                result["rss_found"] = rss_result
        except Exception:
            pass


    enriched.sort(key=_score_candidate, reverse=True)

    # 只保留 top-10
    result["candidates"] = enriched[:10]
    return result


async def probe_with_llm(url: str, items: list[dict], llm) -> dict:
    """LLM 质量评估 — 不猜选择器，只评估已提取的内容质量。

    items: 启发式探测提取到的 [{title, link}, ...]
    返回: {
        "good_indices": [...],
        "junk_indices": [...],
        "quality_score": 0.0-1.0,
        "reasoning": "...",
        "evaluated_items": [...],
    }
    """
    if not items:
        return {"good_indices": [], "junk_indices": [], "quality_score": 0,
                "reasoning": "无条目可评估", "evaluated_items": []}

    try:
        result = await llm.evaluate_extraction_quality(url, items)
    except Exception as e:
        return {"good_indices": list(range(len(items))), "junk_indices": [],
                "quality_score": 0.5, "reasoning": f"LLM 调用失败: {e}",
                "evaluated_items": items}

    good = result.get("good_indices", [])
    junk = result.get("junk_indices", [])
    score = result.get("quality_score", 0.5)
    reasoning = result.get("reasoning", "")

    evaluated = []
    for idx, item in enumerate(items):
        item["_llm_verdict"] = "good" if idx in good else ("junk" if idx in junk else "unknown")
        evaluated.append(item)

    return {
        "good_indices": good,
        "junk_indices": junk,
        "quality_score": score,
        "reasoning": reasoning,
        "evaluated_items": evaluated[:20],
    }