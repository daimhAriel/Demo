"""全流程编排器 — 协调爬虫、LLM、存储、通知"""
import asyncio
import json
import logging
from datetime import datetime

from core.database import DatabaseHandler
from core.crawler import CrawlerManager
from core.llm import LLMProcessor
from core.notifier import Notifier
from core.wechat_fetcher import fetch_articles
from models import SiteConfig

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """协调爬虫、LLM、存储、通知的完整工作流"""

    def __init__(
        self,
        db: DatabaseHandler,
        crawler: CrawlerManager,
        llm: LLMProcessor,
        notifier: Notifier | None = None,
    ):
        self.db = db
        self.crawler = crawler
        self.llm = llm
        self.notifier = notifier or Notifier(db)

    def _get_standard_categories(self, source_type: str = "company") -> list[str]:
        """根据源类型返回分类列表"""
        base = [
            "商业化情况", "战略目标", "产品与工程",
            "适航情况", "产能规划", "股权与资本",
        ]
        if source_type == "media":
            # 垂类媒体额外包含政策法规
            return base + ["政策法规"]
        return base

    async def run_site(self, config: SiteConfig) -> int:
        """
        对一个站点执行一次完整的监控周期。
        返回本次新增的情报数量。
        """
        logger.info(f"[{config.name}] 开始监控周期，URL: {config.url}")
        new_count = 0
        last_url = None
        crawled = 0

        # 预加载本站点已知 URL 集合，用于爬虫前置跳过（避免老文章 Fetch 详情页）
        known_urls = set()
        try:
            rows = self.db.get_site_urls(config.id)
            known_urls = set(r["source_url"] for r in rows)
        except Exception:
            pass

        # 加载公司列表
        companies = self.db.get_company_list()

        # 确定源类型
        source_type = getattr(config, "source_type", "company") or "company"

        # ── 微信公众号 → 走 API 抓取，不走网页爬虫 ──
        if source_type == "wechat":
            wechat_key = self.db.get_app_config("wechat_api_key")
            if not wechat_key:
                logger.warning(f"[{config.name}] 未配置微信公众号 API Key，跳过")
                print(f"  [!] {config.name}: 请先在系统设置中配置微信公众号 API Key")
                return 0

            async for raw_page in fetch_articles(
                fakeid=config.wechat_fakeid,
                api_key=wechat_key,
                site_id=config.id,
                known_urls=known_urls,
                last_seen_url=config.last_seen_url,
                max_pages=config.max_pages or 10,
            ):
                crawled += 1
                fp = DatabaseHandler.make_fingerprint(raw_page.source_url, raw_page.raw_text)
                if self.db.is_duplicate(fp):
                    logger.debug(f"  跳过重复: {raw_page.source_url}")
                    last_url = raw_page.source_url
                    continue

                if self.llm.is_configured:
                    topic = config.topic or "eVTOL and low-altitude economy"
                    standard_cats = self._get_standard_categories("company")
                    item = await self.llm.process(
                        raw_page, topic, standard_cats,
                        source_type="company", companies=companies,
                    )
                    if item is None:
                        logger.debug(f"  LLM标记为无关: {raw_page.title}")
                        continue
                else:
                    from models import IntelItem, normalize_time
                    from core.classifier import classify
                    raw_time = raw_page.time_snippet or raw_page.title
                    event_time = normalize_time(raw_time)
                    category = classify(raw_page.title, raw_page.raw_text)
                    item = IntelItem(
                        fingerprint=fp,
                        summary=raw_page.title,
                        event_time=event_time,
                        category=category,
                        source_url=raw_page.source_url,
                        site_id=raw_page.site_id,
                        raw_title=raw_page.title,
                        clean_title="",
                        company="",
                        crawled_at=raw_page.crawled_at,
                    )

                if self.db.save_intel(item):
                    new_count += 1
                    last_url = raw_page.source_url
                    print(f"  [+] [{item.category}] {item.summary}")
                    print(f"     {raw_page.source_url}")
                    self.notifier.send(item, site_name=config.name)

            # 更新增量标记
            if last_url:
                config.last_seen_url = last_url
                config.last_crawled_at = datetime.now()
                self.db.save_site(config)

            logger.info(f"[{config.name}] 完成: 抓取 {crawled} 条, 新增 {new_count} 条")
            return new_count

        # ── 网页站点 → 走爬虫 ──
        async for raw_page in self.crawler.crawl_site(config, known_urls=known_urls):
            crawled += 1
            # 去重检查
            fp = DatabaseHandler.make_fingerprint(
                raw_page.source_url, raw_page.raw_text)
            if self.db.is_duplicate(fp):
                logger.debug(f"  跳过重复: {raw_page.source_url}")
                last_url = raw_page.source_url
                continue

            # LLM 处理（API Key 已配置则优先用 LLM）
            if self.llm.is_configured:
                topic = config.topic or "eVTOL and low-altitude economy"
                standard_cats = self._get_standard_categories(source_type)
                item = await self.llm.process(
                    raw_page, topic, standard_cats,
                    source_type=source_type, companies=companies,
                )
                if item is None:
                    logger.debug(f"  LLM标记为无关: {raw_page.title}")
                    continue
            else:
                # 无 topic 配置 → 关键词分类（方案A）
                from models import IntelItem, normalize_time
                from core.classifier import classify
                raw_time = raw_page.time_snippet or raw_page.title
                event_time = normalize_time(raw_time)
                category = classify(raw_page.title, raw_page.raw_text)
                item = IntelItem(
                    fingerprint=fp,
                    summary=raw_page.title,
                    event_time=event_time,
                    category=category,
                    source_url=raw_page.source_url,
                    site_id=raw_page.site_id,
                    raw_title=raw_page.title,
                    clean_title="",
                    company="",  # 非LLM模式下暂不填充公司
                    crawled_at=raw_page.crawled_at,
                )

            # 持久化
            if self.db.save_intel(item):
                new_count += 1
                last_url = raw_page.source_url
                print(f"  [+] [{item.category}] {item.summary}")
                print(f"     {raw_page.source_url}")
                # 推送通知
                self.notifier.send(item, site_name=config.name)

        # 更新增量标记
        if last_url:
            config.last_seen_url = last_url
            config.last_crawled_at = datetime.now()
            self.db.save_site(config)

        logger.info(f"[{config.name}] 完成: 抓取 {crawled} 条, 新增 {new_count} 条")
        return new_count

    async def run_all(self) -> dict[str, int]:
        """运行所有启用的站点"""
        sites = self.db.list_sites(enabled_only=True)
        if not sites:
            print("[!] 没有已启用的监控站点，请先添加配置。")
            return {}

        print(f"\n[*] 即将监控 {len(sites)} 个站点...\n")
        results = {}
        tasks = [self.run_site(site) for site in sites]
        counts = await asyncio.gather(*tasks, return_exceptions=True)
        for site, result in zip(sites, counts):
            if isinstance(result, Exception):
                logger.error(f"[{site.name}] 异常: {result}")
                print(f"  [!] {site.name}: 失败 - {result}")
                results[site.id] = 0
            else:
                results[site.id] = result
        return results

    async def reprocess_all(self, force: bool = False) -> int:
        """对数据库已有情报进行 LLM 回填（摘要+分类+时间+公司归属）"""
        from models import RawPage
        items = self.db.get_all_intel_items()
        companies = self.db.get_company_list()
        updated = 0
        skipped = 0
        for idx, item in enumerate(items):
            if not force and item["summary"] != item["raw_title"] and item["category"] != "其他":
                skipped += 1
                continue

            # 获取站点的 source_type
            site_config = None
            try:
                cfg = self.db.get_site_config(item["site_id"])
                if cfg:
                    site_config = json.loads(cfg)
            except Exception:
                pass
            source_type = "company"
            if site_config:
                source_type = site_config.get("source_type", "company") or "company"

            page = RawPage(
                title=item["raw_title"] or "",
                source_url=item["source_url"],
                raw_text=item["raw_title"] or "",
                time_snippet="",
                site_id=item["site_id"],
            )
            cats = self._get_standard_categories(source_type)
            result = await self.llm.process(
                page, "eVTOL and low-altitude economy", cats,
                source_type=source_type, companies=companies,
            )
            if result:
                self.db.update_item_fields(
                    item["fingerprint"],
                    summary=result.summary,
                    clean_title=result.clean_title or "",
                    category=result.category,
                    event_time=result.event_time or item["event_time"],
                    company=result.company or item.get("company", ""),
                    processed_at=datetime.now().isoformat(),
                )
                updated += 1
            if (idx + 1) % 20 == 0:
                logger.info(f"  回填进度: {idx+1}/{len(items)}, 已更新{updated}")
        logger.info(f"回填完成: 共{len(items)}条, 更新{updated}条, 跳过{skipped}条")
        return updated
