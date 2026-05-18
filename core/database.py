"""数据持久化与去重 — SQLite"""
import sqlite3
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from models import SiteConfig, IntelItem


class DatabaseHandler:
    """SQLite 数据库处理器"""

    def __init__(self, db_path: str = ""):
        if not db_path:
            db_path = str(Path(__file__).parent.parent / "data" / "intel.db")
        self.db_path = db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self):
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sites (
                    id TEXT PRIMARY KEY,
                    config TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS intel_items (
                    fingerprint TEXT PRIMARY KEY,
                    event_time TEXT,
                    summary TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    category TEXT DEFAULT '其他',
                    site_id TEXT NOT NULL,
                    raw_title TEXT,
                    company TEXT DEFAULT '',
                    crawled_at TEXT NOT NULL,
                    processed_at TEXT,
                    pushed INTEGER DEFAULT 0,
                    pushed_at TEXT,
                    FOREIGN KEY (site_id) REFERENCES sites(id)
                );

                CREATE TABLE IF NOT EXISTS app_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_intel_site
                    ON intel_items(site_id);
                CREATE INDEX IF NOT EXISTS idx_intel_pushed
                    ON intel_items(pushed);
                CREATE INDEX IF NOT EXISTS idx_intel_crawled
                    ON intel_items(crawled_at);
            """)
            # 迁移：兼容旧数据库无 company 列
            cols = [r[1] for r in conn.execute("PRAGMA table_info(intel_items)").fetchall()]
            if "company" not in cols:
                conn.execute("ALTER TABLE intel_items ADD COLUMN company TEXT DEFAULT ''")
            if "clean_title" not in cols:
                conn.execute("ALTER TABLE intel_items ADD COLUMN clean_title TEXT DEFAULT ''")

    # ── 站点配置 ──

    def save_site(self, config: SiteConfig) -> None:
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sites VALUES (?, ?, ?, ?)",
                (config.id, config.model_dump_json(), now, now))

    def get_site(self, site_id: str) -> Optional[SiteConfig]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT config FROM sites WHERE id = ?",
                (site_id,)).fetchone()
        if row:
            return SiteConfig.model_validate_json(row["config"])
        return None

    def list_sites(self, enabled_only: bool = False) -> list[SiteConfig]:
        with self._get_conn() as conn:
            if enabled_only:
                rows = conn.execute(
                    "SELECT config FROM sites WHERE json_extract(config, '$.enabled') = 1"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT config FROM sites"
                ).fetchall()
        return [SiteConfig.model_validate_json(r["config"]) for r in rows]

    def delete_site(self, site_id: str) -> None:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        # 同步清理公司列表中引用该 site_id 的映射
        changed = False
        companies = self.get_company_list()
        for c in companies:
            if c.get("site_id") == site_id:
                c["site_id"] = ""
                changed = True
        if changed:
            self.save_company_list(companies)

    def clear_all_intel(self) -> int:
        """清空所有情报数据，返回删除条数"""
        with self._get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) as cnt FROM intel_items").fetchone()["cnt"]
            conn.execute("DELETE FROM intel_items")
        return count

    # ── 指纹去重 ──

    @staticmethod
    def make_fingerprint(url: str, raw_text: str) -> str:
        """基于 URL + 前200字符 生成去重指纹"""
        content = f"{url}:{raw_text[:200]}"
        return hashlib.sha256(content.encode()).hexdigest()

    def is_duplicate(self, fingerprint: str) -> bool:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM intel_items WHERE fingerprint = ?",
                (fingerprint,)).fetchone()
        return row is not None

    def save_intel(self, item: IntelItem) -> bool:
        """保存情报，返回 True=新增, False=重复跳过"""
        if self.is_duplicate(item.fingerprint):
            return False
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO intel_items
                   (fingerprint, event_time, summary, source_url, category,
                    site_id, raw_title, clean_title, company, crawled_at, processed_at, pushed, pushed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item.fingerprint, item.event_time, item.summary,
                 item.source_url, item.category, item.site_id,
                 item.raw_title, item.clean_title, item.company,
                 item.crawled_at.isoformat(),
                 item.processed_at.isoformat() if item.processed_at else None,
                 int(item.pushed),
                 item.pushed_at.isoformat() if item.pushed_at else None))
        return True

    # ── 应用配置（LLM 等） ──

    def get_app_config(self, key: str, default: str = "") -> str:
        """读取应用配置"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM app_config WHERE key=?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_app_config(self, key: str, value: str):
        """写入应用配置"""
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO app_config (key, value) VALUES (?, ?)",
                (key, value)
            )

    def get_all_app_config(self) -> dict:
        """获取所有应用配置"""
        with self._get_conn() as conn:
            rows = conn.execute("SELECT key, value FROM app_config").fetchall()
            return {r["key"]: r["value"] for r in rows}

    def delete_app_config(self, key: str):
        """删除指定配置项"""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM app_config WHERE key=?", (key,))

    # ── 查询 ──

    def get_intel_items(
        self, limit: int = 50, offset: int = 0,
        site_id: Optional[str] = None,
        category: Optional[str] = None,
        company: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> list[dict]:
        with self._get_conn() as conn:
            conditions = []
            params = []
            if site_id:
                conditions.append("i.site_id = ?")
                params.append(site_id)
            if category:
                conditions.append("i.category = ?")
                params.append(category)
            if company == "__other__":
                # 其他 = 公司字段不匹配任何已知公司
                known_ids = [c["id"] for c in self.get_company_list()]
                if known_ids:
                    not_clauses = " AND ".join(["INSTR(i.company, ?) = 0"] * len(known_ids))
                    conditions.append(f"({not_clauses} OR i.company = '' OR i.company IS NULL)")
                    params.extend(known_ids)
                else:
                    conditions.append("(i.company = '' OR i.company IS NULL)")
            elif company:
                conditions.append("INSTR(i.company, ?) > 0")
                params.append(company)
            if date_from:
                conditions.append("i.event_time >= ?")
                params.append(date_from)
            if date_to:
                conditions.append("i.event_time <= ?")
                params.append(date_to)
            where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
            rows = conn.execute(
                f"SELECT i.*, s.config FROM intel_items i "
                f"LEFT JOIN sites s ON i.site_id = s.id{where} "
                f"ORDER BY i.event_time IS NULL, i.event_time DESC, i.crawled_at DESC "
                f"LIMIT ? OFFSET ?",
                (*params, limit, offset)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_intel_items(self) -> list[dict]:
        """获取全部情报（无限制），用于批量回填"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM intel_items ORDER BY crawled_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_site_urls(self, site_id: str) -> list[dict]:
        """获取指定站点的所有 source_url，用于爬虫前置跳过"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT source_url FROM intel_items WHERE site_id = ?",
                (site_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_items_by_category_grouped_by_site(self, category: str) -> dict[str, int]:
        """返回指定类别中各站点的情报数量"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT site_id, COUNT(*) as cnt FROM intel_items "
                "WHERE category = ? GROUP BY site_id ORDER BY cnt DESC",
                (category,)
            ).fetchall()
        return {r["site_id"]: r["cnt"] for r in rows}

    def get_items_by_category_grouped_by_company(
        self, category: str, date_from: str = "", date_to: str = ""
    ) -> dict[str, int]:
        """返回指定类别中各公司的情报数量（company 不为空时），支持日期筛选"""
        with self._get_conn() as conn:
            sql = (
                "SELECT company, COUNT(*) as cnt FROM intel_items "
                "WHERE category = ? AND company != ''"
            )
            params = [category]
            if date_from:
                sql += " AND event_time >= ?"
                params.append(date_from)
            if date_to:
                sql += " AND event_time <= ?"
                params.append(date_to)
            sql += " GROUP BY company ORDER BY cnt DESC"
            rows = conn.execute(sql, params).fetchall()
        return {r["company"]: r["cnt"] for r in rows}

    def get_category_trend(self, date_from: str, date_to: str) -> list[dict]:
        """按月份分组统计各分类情报数量，用于堆叠面积图"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT strftime('%Y-%m', event_time) as month, category, COUNT(*) as cnt "
                "FROM intel_items "
                "WHERE event_time >= ? AND event_time <= ? AND category != '其他' "
                "GROUP BY month, category ORDER BY month",
                (date_from, date_to)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_company_trend(self, date_from: str, date_to: str) -> list[dict]:
        """按月份分组统计各公司情报数量，用于折线图"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT strftime('%Y-%m', event_time) as month, company, COUNT(*) as cnt "
                "FROM intel_items "
                "WHERE event_time >= ? AND event_time <= ? AND company != '' "
                "GROUP BY month, company ORDER BY month",
                (date_from, date_to)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── 统计 ──

    def get_stats(self, date_from: str = "", date_to: str = "") -> dict:
        with self._get_conn() as conn:
            # 总情报数（支持时间筛选）
            if date_from and date_to:
                total = conn.execute(
                    "SELECT COUNT(*) as cnt FROM intel_items "
                    "WHERE event_time >= ? AND event_time <= ?",
                    (date_from, date_to)
                ).fetchone()["cnt"]
                unread = conn.execute(
                    "SELECT COUNT(*) as cnt FROM intel_items "
                    "WHERE pushed = 0 AND event_time >= ? AND event_time <= ?",
                    (date_from, date_to)
                ).fetchone()["cnt"]
            else:
                total = conn.execute(
                    "SELECT COUNT(*) as cnt FROM intel_items").fetchone()["cnt"]
                unread = conn.execute(
                    "SELECT COUNT(*) as cnt FROM intel_items WHERE pushed = 0"
                ).fetchone()["cnt"]
            site_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM sites").fetchone()["cnt"]
            by_category = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM intel_items "
                "GROUP BY category ORDER BY cnt DESC"
            ).fetchall()
        return {
            "total_items": total,
            "unread_items": unread,
            "total_sites": site_count,
            "by_category": {r["category"]: r["cnt"] for r in by_category},
        }

    def update_item_fields(self, fingerprint: str, **kwargs) -> bool:
        """更新指定情报的字段（按 fingerprint 定位）"""
        allowed = {"summary", "category", "event_time", "clean_title", "raw_title", "processed_at", "pushed", "pushed_at", "company"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return False
        with self._get_conn() as conn:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [fingerprint]
            conn.execute(f"UPDATE intel_items SET {set_clause} WHERE fingerprint = ?", values)
            conn.commit()
        return True

    # ── 公司列表管理 ──

    def get_company_list(self) -> list[dict]:
        """从 app_config 读取公司列表"""
        js = self.get_app_config("company_list", "[]")
        return json.loads(js)

    def save_company_list(self, companies: list[dict]):
        """保存公司列表到 app_config"""
        self.set_app_config("company_list", json.dumps(companies, ensure_ascii=False))

    def get_site_company_name(self, site_id: str) -> str:
        """根据 site_id 查找关联的公司名（企业官网）"""
        config = self.get_site_config(site_id)
        if not config:
            return ""
        cfg = json.loads(config)
        if cfg.get("source_type", "company") != "company":
            return ""
        companies = self.get_company_list()
        for c in companies:
            if c.get("site_id") == site_id:
                return c.get("name", "")
        return cfg.get("name", site_id)
