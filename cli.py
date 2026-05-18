#!/usr/bin/env python3
"""CLI 入口 — 测试核心链路"""
import asyncio
import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).parent))

from core.database import DatabaseHandler
from core.crawler import CrawlerManager
from core.llm import LLMProcessor
from core.pipeline import PipelineOrchestrator
from models import SiteConfig
import config as app_config


# ── 预设 Demo 站点配置 ──

DEMO_SITES = [
    SiteConfig(
        id="gh_blog",
        name="GitHub Blog",
        url="https://github.com/blog",
        mode="static",
        list_selector="main",
        item_selector="article.post-card",
        title_selector="h2 a, h3 a",
        link_selector="h2 a, h3 a",
        time_selector="time",
        content_selector=None,
        pagination=None,
        max_pages=1,
        follow_detail=False,
        delay_min=0.5,
        delay_max=1.5,
        topic="",
        categories=["技术更新", "行业动态", "安全漏洞", "公司新闻"],
        cron_expr="0 */6 * * *",
    ),
]


async def cmd_seed(db: DatabaseHandler):
    """注入预设站点配置"""
    for site in DEMO_SITES:
        db.save_site(site)
        print(f"  ✅ 已写入: {site.name} ({site.id})")
    print(f"\n📊 当前共有 {len(db.list_sites())} 个站点配置")


async def cmd_list(db: DatabaseHandler):
    """列出所有站点"""
    sites = db.list_sites()
    if not sites:
        print("⚠️  没有站点配置。运行 python cli.py seed 添加预设站点。")
        return
    print(f"{'ID':<20} {'名称':<25} {'状态':<8} {'模式':<8} 主题")
    print("-" * 80)
    for s in sites:
        status = "🟢 启用" if s.enabled else "🔴 停用"
        topic = s.topic if s.topic else "(无)"
        print(f"{s.id:<20} {s.name:<25} {status:<8} {s.mode:<8} {topic}")


async def cmd_run(db: DatabaseHandler, site_id: str = ""):
    """执行爬取"""
    crawler = CrawlerManager()
    # 从数据库读取 LLM 配置（优先），环境变量兜底
    _cfg = db.get_all_app_config()
    llm = LLMProcessor(
        mode=_cfg.get("llm_mode") or app_config.LLM_MODE,
        api_key=_cfg.get("llm_api_key") or app_config.OPENAI_API_KEY,
        base_url=_cfg.get("llm_base_url") or app_config.OPENAI_BASE_URL,
        model=_cfg.get("llm_model") or app_config.LLM_MODEL,
        ollama_base_url=_cfg.get("llm_ollama_url") or app_config.OLLAMA_BASE_URL,
    )
    orchestrator = PipelineOrchestrator(db, crawler, llm)

    try:
        if site_id:
            config = db.get_site(site_id)
            if not config:
                print(f"❌ 未找到站点: {site_id}")
                return
            print(f"\n🚀 开始监控: {config.name}")
            await orchestrator.run_site(config)
        else:
            await orchestrator.run_all()
    finally:
        await crawler.close()


async def cmd_stats(db: DatabaseHandler):
    """查看统计"""
    stats = db.get_stats()
    print("\n📊 系统统计")
    print(f"  站点总数:   {stats['total_sites']}")
    print(f"  情报总数:   {stats['total_items']}")
    print(f"  未推送:     {stats['unread_items']}")
    if stats["by_category"]:
        print(f"\n  分类分布:")
        for cat, cnt in stats["by_category"].items():
            print(f"    {cat}: {cnt}")


async def cmd_last(db: DatabaseHandler, n: int = 10):
    """查看最近情报"""
    items = db.get_intel_items(limit=n)
    if not items:
        print("⚠️  暂无情报数据。")
        return
    print(f"\n📋 最近 {len(items)} 条情报:\n")
    for item in items:
        evt = item.get("event_time") or ""
        evt_str = f"  📅 事件: {evt}" if evt else ""
        print(f"  [{item['category']}] {item['summary']}")
        print(f"    {item['source_url']}")
        if evt_str:
            print(evt_str)
        print(f"    抓取: {item['crawled_at']}")
        print()


async def cmd_reprocess(db: DatabaseHandler, force: bool = False, model: str = ""):
    """LLM 回填已有情报的摘要+分类+时间"""
    cfg = db.get_all_app_config()
    llm = LLMProcessor(
        mode=cfg.get("llm_mode", "openai"),
        api_key=cfg.get("llm_api_key", ""),
        base_url=cfg.get("llm_base_url", ""),
        model=model or cfg.get("llm_model", "gpt-4o-mini"),
    )
    if not llm.is_configured:
        print("❌ LLM 未配置 API Key，请在面板系统设置中配置后重试。")
        return
    pipeline = PipelineOrchestrator(db, CrawlerManager(), llm)
    updated = await pipeline.reprocess_all(force=force)
    stats = db.get_stats()
    print(f"\n✅ 回填完成！更新 {updated} 条情报")
    print(f"   当前总计: {stats['total_items']} 条")
    for cat, cnt in stats["by_category"].items():
        print(f"     {cat}: {cnt}")


def _fix_encoding():
    """Fix GBK encoding issues on Windows"""
    import sys, io
    if sys.stdout.encoding and sys.stdout.encoding.lower() in ('gbk', 'gb2312', 'gb18030'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')


def main():
    _fix_encoding()
    import argparse
    parser = argparse.ArgumentParser(description="🤖 情报监控 Agent CLI")
    parser.add_argument("command", nargs="?", default="help",
                        choices=["seed", "seed-companies", "list", "run", "stats", "last", "reprocess", "help"])
    parser.add_argument("--site", "-s", default="", help="站点 ID（仅 run 命令）")
    parser.add_argument("--limit", "-n", type=int, default=10, help="最近情报数量（last 命令）")
    parser.add_argument("--db", default="", help="数据库路径")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")
    parser.add_argument("--force", "-f", action="store_true", help="强制重新处理所有情报")
    parser.add_argument("--model", default="", help="LLM 模型名（覆盖配置文件）")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    db = DatabaseHandler(db_path=args.db)

    if args.command == "seed":
        asyncio.run(cmd_seed(db))
    elif args.command == "seed-companies":
        asyncio.run(cmd_seed_companies(db))
    elif args.command == "list":
        asyncio.run(cmd_list(db))
    elif args.command == "run":
        asyncio.run(cmd_run(db, args.site))
    elif args.command == "stats":
        asyncio.run(cmd_stats(db))
    elif args.command == "last":
        asyncio.run(cmd_last(db, args.limit))
    elif args.command == "reprocess":
        asyncio.run(cmd_reprocess(db, force=args.force, model=args.model))
    else:
        parser.print_help()
        print("\n📖 常用命令示例:")
        print("  python cli.py seed                # 注入预设站点")
        print("  python cli.py seed-companies       # 从已有站点生成公司列表")
        print("  python cli.py list                # 查看站点列表")
        print("  python cli.py run                 # 执行全部站点")
        print("  python cli.py run -s hn_frontpage # 只跑一个站")
        print("  python cli.py stats               # 查看统计")
        print("  python cli.py last -n 20          # 最近20条情报")
        print("  python cli.py reprocess           # LLM回填已有情报")
        print("  python cli.py reprocess -f        # 强制全部重做")


async def cmd_seed_companies(db: DatabaseHandler):
    """根据现有站点初始化公司列表（用于公司归属）"""
    sites = db.list_sites(enabled_only=False)
    existing = {c["id"] for c in db.get_company_list()}
    new_companies = []
    for s in sites:
        cfg_dict = {}
        try:
            import json
            raw = db.get_site_config(s.id)
            if raw:
                cfg_dict = json.loads(raw)
        except Exception:
            pass
        st = cfg_dict.get("source_type", "company")
        if st != "company":
            continue
        if s.id in existing:
            continue
        new_companies.append({
            "id": s.id,
            "name": s.name,
            "keywords": [s.id, s.name.lower().replace(" ", "_")],
            "site_id": s.id,
        })
    if new_companies:
        all_companies = db.get_company_list() + new_companies
        db.save_company_list(all_companies)
        for c in new_companies:
            print(f"  ✅ 新增公司: {c['name']} ({c['id']})")
    else:
        print("  ℹ️  无需新增公司条目")
    print(f"\n📋 当前公司列表共 {len(db.get_company_list())} 项")


if __name__ == "__main__":
    main()
