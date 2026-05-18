"""爬虫引擎管理器 — 支持静态(BS4)与动态(Playwright)双模式 + RSS Feed"""
import asyncio
import random
import re
from typing import AsyncGenerator, Optional

import feedparser
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from models import SiteConfig, RawPage


class CrawlerManager:
    """多模式爬虫引擎"""

    # 共享浏览器实例（避免重复启动 Chromium 导致 Windows 多实例冲突）
    _shared_browser = None
    _shared_playwright = None

    UA_POOL = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
        "Gecko/20100101 Firefox/125.0",
    ]

    def __init__(self):
        self._browser = None
        self._playwright = None

    async def _random_delay(self, config: SiteConfig):
        delay = random.uniform(config.delay_min, config.delay_max)
        await asyncio.sleep(delay)

    def _get_headers(self, config: SiteConfig) -> dict:
        headers = dict(config.headers) if config.headers else {}
        headers.setdefault("User-Agent", random.choice(self.UA_POOL))
        headers.setdefault("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
        return headers

    # ── RSS Feed 抓取 ──

    async def fetch_feed(self, config: SiteConfig) -> list[dict]:
        """从配置的 feed_url 抓取并解析 RSS/Atom feed。
        返回 [{title, link, time_snippet, summary}, ...]
        无 feed_url 或解析失败返回空列表。
        """
        if not config.feed_url:
            return []
        try:
            f = feedparser.parse(config.feed_url)
        except Exception as e:
            print(f"  [RSS失败] {config.feed_url}: {e}")
            return []
        if f.bozo and not f.entries:
            print(f"  [RSS失败] {config.feed_url}: bozo={f.bozo_exception}")
            return []
        results = []
        for entry in f.entries[:50]:
            results.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "time_snippet": entry.get("published", entry.get("updated", "")),
                "summary": entry.get("summary", "")[:200] if entry.get("summary") else "",
            })
        print(f"  [RSS] {config.feed_url} → {len(results)} 条")
        return results

    async def _launch_browser(self):
        if CrawlerManager._shared_browser is None:
            CrawlerManager._shared_playwright = await async_playwright().start()
            CrawlerManager._shared_browser = await CrawlerManager._shared_playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
                ignore_default_args=["--enable-automation"],
            )
        self._browser = CrawlerManager._shared_browser
        self._playwright = CrawlerManager._shared_playwright

    async def _reboot_browser(self):
        """重启共享浏览器（连接失效时调用）"""
        if CrawlerManager._shared_browser:
            try:
                await CrawlerManager._shared_browser.close()
            except Exception:
                pass
        if CrawlerManager._shared_playwright:
            try:
                await CrawlerManager._shared_playwright.stop()
            except Exception:
                pass
        CrawlerManager._shared_browser = None
        CrawlerManager._shared_playwright = None
        await self._launch_browser()

    async def close(self):
        # 不关闭共享浏览器，仅清空本实例引用
        self._browser = None
        self._playwright = None

    @classmethod
    async def close_global(cls):
        """全局关闭共享浏览器（应用退出时调用）"""
        if cls._shared_browser:
            try:
                await cls._shared_browser.close()
            except Exception:
                pass
            cls._shared_browser = None
        if cls._shared_playwright:
            try:
                await cls._shared_playwright.stop()
            except Exception:
                pass
            cls._shared_playwright = None

    # ── 动态模式 ──

    async def _fetch_dynamic(self, url: str, config: SiteConfig) -> str:
        # 先确保浏览器已启动
        await self._launch_browser()
        if CrawlerManager._shared_browser is None:
            raise RuntimeError("浏览器启动失败")

        for attempt in range(3):
            try:
                context = await self._browser.new_context(
                    user_agent=random.choice(self.UA_POOL),
                    viewport={"width": 1920, "height": 1080},
                    locale="en-US",
                    proxy={"server": config.proxy} if config.proxy else None,
                )
                # 拦截不必要的资源以加速
                await context.route(
                    re.compile(r"\.(png|jpg|jpeg|gif|svg|woff|woff2|ttf|eot)($|\?)"),
                    lambda route: route.abort(),
                )
                page = await context.new_page()
            except Exception as e:
                # 浏览器连接断开 → 重启后重试
                await self._reboot_browser()
                if attempt == 2:
                    raise RuntimeError(f"浏览器创建失败(已重试3次): {e}")
                await asyncio.sleep(1)
                continue

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(3000)
                html = await page.content()
                await context.close()
                return html
            except Exception as e:
                await context.close()
                if "ERR_CONNECTION" in str(e) and attempt == 0:
                    # 连接失败时重启浏览器
                    await self._reboot_browser()
                await asyncio.sleep(2)
        raise RuntimeError(f"无法访问: {url}")


    # ── 静态模式 ──

    async def _fetch_static(self, url: str, config: SiteConfig) -> str:
        import httpx
        headers = self._get_headers(config)
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30,
        ) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text

    # ── 统一入口 ──

    async def fetch_page(self, url: str, config: SiteConfig) -> str:
        if config.mode == "dynamic":
            return await self._fetch_dynamic(url, config)
        return await self._fetch_static(url, config)

    # ── 解析列表页 ──

    def parse_list_page(
        self, html: str, config: SiteConfig
    ) -> list[dict]:
        """解析列表页，返回 [{title, link, time_snippet}, ...]"""
        soup = BeautifulSoup(html, "lxml")
        # 1. 如果有特殊区块定位，先缩小到该区块
        if config.section_selector:
            section = soup.select_one(config.section_selector)
            base = section if section else soup
        else:
            base = soup
        # 2. 在区块内找列表容器
        container = base.select_one(config.list_selector) if config.list_selector else base
        # 3. 在容器内找条目
        items = container.select(config.item_selector) if config.item_selector else []
        results = []
        for item in items:
            title_el = item.select_one(config.title_selector) if config.title_selector else item
            link_el = item.select_one(config.link_selector) if config.link_selector else (
                item if item.name == "a" else item.find("a")
            )
            if not link_el or not link_el.get("href"):
                # 无链接时用占位
                link = config.url
            else:
                link = link_el.get("href", "")
            if link.startswith("/"):
                # 尝试从配置 URL 中提取 base
                from urllib.parse import urlparse
                parsed = urlparse(config.url)
                base = f"{parsed.scheme}://{parsed.netloc}"
                link = base + link
            elif link.startswith("//"):
                link = "https:" + link
            elif not link.startswith("http"):
                from urllib.parse import urljoin
                link = urljoin(config.url, link)

            title_text = title_el.get_text(strip=True) if title_el is not None else ""
            result = {
                "title": title_text,
                "link": link,
            }
            if config.time_selector:
                time_el = item.select_one(config.time_selector)
                if time_el:
                    result["time_snippet"] = time_el.get_text(strip=True)
            results.append(result)
        return results

    # ── 提取详情页内容 ──

    async def fetch_detail(self, url: str, config: SiteConfig) -> str:
        try:
            html = await self.fetch_page(url, config)
        except Exception as e:
            return f"[抓取详情页失败: {e}]"

        # 尝试主内容选择器 → 备选内容选择器 → 全页降序
        for sel in [config.content_selector, config.alt_content_selector]:
            if sel:
                soup = BeautifulSoup(html, "lxml")
                el = soup.select_one(sel)
                if el:
                    return el.get_text(separator="\n", strip=True)

        # 全页降序
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)

    async def fetch_detail_title(self, url: str, config: SiteConfig) -> str:
        """提取详情页标题（使用 detail_title_selector 或降序）"""
        try:
            html = await self.fetch_page(url, config)
        except Exception as e:
            return ""
        soup = BeautifulSoup(html, "lxml")
        if config.detail_title_selector:
            el = soup.select_one(config.detail_title_selector)
            if el:
                return el.get_text(strip=True)
        # 降序：<title> 或 <h1>
        title_tag = soup.find("title")
        if title_tag:
            return title_tag.get_text(strip=True)
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(strip=True)
        return ""

    # ── 点击翻页展开（dynamic 模式） ──

    async def _expand_with_clicks(self, url: str, config: SiteConfig) -> str:
        """
        动态模式下反复点击 "Load More" 按钮，返回展开后的完整 HTML。

        策略：
          1. 每轮查找所有匹配的可见按钮并逐一点击
          2. 每个按钮点击后等 2s 让新内容加载
          3. 一轮结束后，如果还有按钮 → 继续下一轮（最多 max_pages 轮）
          4. 无按钮或达到轮数上限后返回最终 HTML
        """
        await self._launch_browser()
        context = await self._browser.new_context(
            user_agent=random.choice(self.UA_POOL),
            proxy={"server": config.proxy} if config.proxy else None,
        )
        page = await context.new_page()
        await page.goto(url, wait_until="networkidle", timeout=30000)

        click_selector = config.pagination.get("selector", "")
        max_rounds = config.max_pages or 5

        for _ in range(max_rounds):
            try:
                buttons = await page.query_selector_all(click_selector)
                visible_buttons = []
                for btn in buttons:
                    try:
                        if await btn.is_visible():
                            visible_buttons.append(btn)
                    except Exception:
                        continue
                if not visible_buttons:
                    break
                for btn in visible_buttons:
                    try:
                        await btn.click()
                        await page.wait_for_timeout(2000)
                    except Exception:
                        continue
            except Exception:
                break

        html = await page.content()
        await context.close()
        return html

    # ── 翻页 URL 生成 ──

    def _get_page_urls(self, config: SiteConfig) -> list[str]:
        """根据翻页配置生成所有页面 URL 列表（支持 query/selector/click 三种模式）"""
        if not config.pagination:
            return [config.url]

        ptype = config.pagination.get("type", "selector")
        # click 类型：只有一页，靠点击展开内容
        if ptype == "click":
            return [config.url]

        # ── URL 参数翻页：?page=1, ?page=2 …（一次性生成） ──
        if ptype == "query":
            start = config.pagination.get("from", 2)
            end = config.pagination.get("to", config.max_pages)
            urls = [config.url]  # page 1

            # 路径模式：/news/page/{n}/
            pattern = config.pagination.get("pattern", "")
            if pattern:
                from urllib.parse import urlparse
                base = urlparse(config.url)
                for i in range(start, end + 1):
                    path = pattern.replace("{n}", str(i))
                    new_url = f"{base.scheme}://{base.netloc}{path}"
                    urls.append(new_url)
                return urls[:config.max_pages]

            # 参数模式：?page=N
            param = config.pagination.get("param", "page")
            from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
            parsed = urlparse(config.url)
            base_query = parse_qs(parsed.query, keep_blank_values=True)
            for i in range(start, end + 1):
                q = dict(base_query)
                q[param] = [str(i)]
                new_url = urlunparse(parsed._replace(query=urlencode(q, doseq=True)))
                urls.append(new_url)
            return urls[:config.max_pages]

        # ── 选择器翻页：沿用原有的动态查找逻辑（默认模式） ──
        return [config.url]

    def _get_next_url(self, html: str, config: SiteConfig) -> Optional[str]:
        if not config.pagination:
            return None
        # click 类型已由 _expand_with_clicks 处理完所有展开
        if config.pagination.get("type") == "click":
            return None
        selector = config.pagination.get("selector", "")
        if not selector:
            return None
        soup = BeautifulSoup(html, "lxml")
        el = soup.select_one(selector)
        if not el:
            return None
        href = el.get("href")
        if not href:
            return None
        if href.startswith("/"):
            from urllib.parse import urlparse
            parsed = urlparse(config.url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            href = base + href
        elif not href.startswith("http"):
            from urllib.parse import urljoin
            href = urljoin(config.url, href)
        return href

    # ── 完整爬取站点 ──

    async def crawl_site(
        self, config: SiteConfig, known_urls: Optional[set] = None
    ) -> AsyncGenerator[RawPage, None]:
        """
        爬取一个站点的完整流程（保守翻页 + 锚点截断）。

        核心策略：
          1. 每页先扫描锚点（last_seen_url）位置
          2. 只 yield 锚点之前的文章（真正的新内容）
          3. 锚点之后的旧文章不耗任何资源
          4. 锚点在当前页 → 本页停止，不翻下一页
          5. 锚点不在当前页 → 翻到下一页（最多 max_pages 页）

        Args:
            known_urls: 数据库中已有的 URL 集合，用于前置跳过（避免旧文章 Fetch 详情页）
        """
        seen_urls = set()
        if config.last_seen_url:
            seen_urls.add(config.last_seen_url)

        # ── RSS Feed 模式：走 feed 而非 HTML 解析 ──
        if config.feed_url:
            feed_items = await self.fetch_feed(config)
            if not feed_items:
                return
            for item in feed_items:
                if item["link"] in seen_urls:
                    break
                raw = RawPage(
                    source_url=item["link"],
                    title=item["title"],
                    raw_text=item.get("summary", item["title"]),
                    html_snippet="",
                    time_snippet=item.get("time_snippet", ""),
                    site_id=config.id,
                )
                seen_urls.add(item["link"])
                yield raw
            return

        ptype = (config.pagination or {}).get("type", "selector")
        urls = self._get_page_urls(config)
        page_count = 0
        current_url = config.url
        url_index = 0

        while True:
            # ── 决定本次要抓的 URL ──
            if ptype == "query":
                if url_index >= len(urls):
                    break
                current_url = urls[url_index]
                url_index += 1
            else:
                if page_count >= config.max_pages:
                    break
                page_count += 1

            # ── 请求页面 ──
            await self._random_delay(config)
            try:
                if ptype == "click":
                    html = await self._expand_with_clicks(current_url, config)
                else:
                    html = await self.fetch_page(current_url, config)
            except Exception as e:
                print(f"  [爬取失败] {current_url}: {e}")
                break

            items = self.parse_list_page(html, config)
            if not items:
                print(f"  [无内容] 第 {page_count} 页无解析结果")
                break

            # ── 预扫描：找到锚点位置 ──
            anchor_idx = None
            for i, item in enumerate(items):
                if item["link"] in seen_urls:
                    anchor_idx = i
                    break

            # ── 只 yield 锚点之前的文章 ──
            #   anchor_idx=None  → 整页都 yield
            #   anchor_idx=N     → yield items[0:N]
            limit = anchor_idx if anchor_idx is not None else len(items)
            for item in items[:limit]:
                link = item["link"]

                # URL 前置检查：已存在的 URL 完全跳过（不 Fetch 详情、不 yield）
                if known_urls and link in known_urls:
                    continue

                content = item["title"]
                detail_title = item["title"]
                if config.follow_detail and link:
                    content = await self.fetch_detail(link, config)
                    if config.detail_title_selector:
                        dt = await self.fetch_detail_title(link, config)
                        if dt:
                            detail_title = dt
                yield RawPage(
                    source_url=link,
                    title=detail_title,
                    raw_text=content,
                    time_snippet=item.get("time_snippet", ""),
                    site_id=config.id,
                )

            # ── 锚点命中 → 停止（不翻下一页） ──
            if anchor_idx is not None:
                print(
                    f"  [锚点截断] 第{page_count}页#{anchor_idx+1} "
                    f"命中: {items[anchor_idx]['link'][:60]}"
                )
                return

            # ── 锚点未命中 → 翻到下一页（仅 selector 模式） ──
            if ptype != "query":
                next_url = self._get_next_url(html, config)
                if not next_url or next_url == current_url:
                    break
                current_url = next_url
            # query 模式：while 循环自动取 urls 中的下一个 URL
