"""
APScheduler 定时爬取调度器
- 全局 cron 表达式（默认每周日午夜）
- 逐站 cron（字段保留，未来切换）
- 启停状态持久化到 app_config 表
"""
import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from core.database import DatabaseHandler
from core.crawler import CrawlerManager
from core.llm import LLMProcessor
from core.pipeline import PipelineOrchestrator

logger = logging.getLogger(__name__)

DEFAULT_CRON = "0 0 * * 0"  # 每周一午夜（APScheduler 此环境 0=周一）


def _parse_cron(cron_expr: str) -> dict:
    """将 "0 0 * * 0" 解析为 CronTrigger 关键字参数"""
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"无效 cron 表达式（需5段）: {cron_expr}")
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "day_of_week": parts[4],
    }


class CrawlScheduler:
    """定时爬取调度器 — 嵌入 Streamlit 进程的后台线程"""

    def __init__(self, db: DatabaseHandler):
        self.db = db
        self.scheduler = BackgroundScheduler(daemon=True)
        self._pipeline: PipelineOrchestrator | None = None
        self._job_id = "crawl_all_sites"

    # ── 内部 ──

    def _build_pipeline(self) -> PipelineOrchestrator:
        if self._pipeline is None:
            crawler = CrawlerManager()
            cfg = self.db.get_all_app_config()
            llm = LLMProcessor(
                mode=cfg.get("llm_mode", "openai"),
                api_key=cfg.get("llm_api_key", ""),
                base_url=cfg.get("llm_base_url", ""),
                model=cfg.get("llm_model", "gpt-4o-mini"),
            )
            self._pipeline = PipelineOrchestrator(self.db, crawler, llm)
        return self._pipeline

    def _run_all_job(self):
        """APScheduler 回调（同步 → 异步）"""
        pipeline = self._build_pipeline()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(pipeline.run_all())
            total = sum(results.values())
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            logger.info(f"[{now}] 定时爬取完成: {total} 条新情报")
            # 保存执行记录
            self.db.set_app_config("last_scheduler_run_time", now)
            self.db.set_app_config("last_scheduler_new_count", str(total))
            self.db.set_app_config("last_scheduler_status", "success")
            self.db.set_app_config("last_scheduler_error", "")
        except Exception as e:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            logger.error(f"定时爬取出错: {e}")
            self.db.set_app_config("last_scheduler_run_time", now)
            self.db.set_app_config("last_scheduler_new_count", "0")
            self.db.set_app_config("last_scheduler_status", "failed")
            self.db.set_app_config("last_scheduler_error", str(e))
        finally:
            loop.close()

    # ── 生命周期 ──

    def start(self) -> bool:
        """启动调度器。已在运行则返回 False。"""
        if self.scheduler.running:
            return False

        cron_expr = self.db.get_app_config("scheduler_cron") or DEFAULT_CRON
        try:
            kwargs = _parse_cron(cron_expr)
        except ValueError:
            logger.warning(f"cron 解析失败，使用默认: {cron_expr}")
            kwargs = _parse_cron(DEFAULT_CRON)

        self.scheduler.add_job(
            self._run_all_job,
            CronTrigger(**kwargs),
            id=self._job_id,
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info(f"调度器已启动, cron={cron_expr}")
        return True

    def stop(self) -> bool:
        """停止调度器。未运行则返回 False。"""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            self.scheduler = BackgroundScheduler(daemon=True)
            logger.info("调度器已停止")
            return True
        return False

    # ── 查询 ──

    @property
    def running(self) -> bool:
        return self.scheduler.running

    def get_next_run_time(self) -> str:
        job = self.scheduler.get_job(self._job_id)
        if job and job.next_run_time:
            return job.next_run_time.strftime("%Y-%m-%d %H:%M")
        return "—"

    def get_cron_expr(self) -> str:
        return self.db.get_app_config("scheduler_cron") or DEFAULT_CRON

    def update_cron(self, cron_expr: str) -> None:
        """更新 cron 并重启调度器"""
        self.db.set_app_config("scheduler_cron", cron_expr)
        if self.running:
            self.stop()
            self.start()
