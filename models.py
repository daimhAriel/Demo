"""数据模型定义"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class SiteConfig(BaseModel):
    """站点爬虫配置"""
    id: str
    name: str
    url: str
    enabled: bool = True

    # 爬取模式
    mode: str = "static"                     # "static" | "dynamic"

    # DOM 选择器
    list_selector: str = ""
    item_selector: str = ""
    title_selector: str = ""
    link_selector: str = ""
    time_selector: Optional[str] = None
    section_selector: str = ""             # 特殊区块定位（如 Latest News）, 可选
    content_selector: Optional[str] = None

    # 翻页
    pagination: Optional[dict] = None
    max_pages: int = 1

    # 子页面
    follow_detail: bool = True
    content_selector: Optional[str] = None
    alt_content_selector: Optional[str] = None       # 备选内容选择器（子页面结构不同时）
    detail_title_selector: Optional[str] = None      # 详情页标题选择器（可选）

    # 反爬
    delay_min: float = 1.0
    delay_max: float = 3.0
    proxy: Optional[str] = None
    headers: Optional[dict] = None

    # RSS / Feed
    feed_url: str = ""

    # 微信公众号
    wechat_fakeid: str = ""              # 公众号 fakeid（用于 API 拉取文章）

    # LLM
    topic: str = ""
    llm_model: str = "gpt-4o-mini"
    source_type: str = "company"           # "company" | "media" | "policy"
    categories: list[str] = Field(default_factory=lambda: ["其他"])

    # 调度
    cron_expr: str = "0 */6 * * *"

    # 增量标记
    last_crawled_at: Optional[datetime] = None
    last_seen_url: Optional[str] = None


class RawPage(BaseModel):
    """爬虫原始结果"""
    source_url: str
    title: str
    raw_text: str
    html_snippet: str = ""
    time_snippet: str = ""       # HTML中提取的原始时间文本（Plan A）
    crawled_at: datetime = Field(default_factory=datetime.now)
    site_id: str


class IntelItem(BaseModel):
    """清洗后的情报条目"""
    fingerprint: str
    event_time: Optional[str] = None
    summary: str
    source_url: str
    category: str = "其他"
    site_id: str
    raw_title: str
    clean_title: str = ""                # LLM清洗后的标题
    company: str = ""                      # 公司归属，媒体文章由LLM/关键词填充
    crawled_at: datetime
    processed_at: Optional[datetime] = None
    pushed: bool = False
    pushed_at: Optional[datetime] = None


# ── 时间文本标准化（Plan A 兜底） ──

import re

_MONTH_MAP = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "jun": "06", "jul": "07", "aug": "08", "sep": "09",
    "oct": "10", "nov": "11", "dec": "12",
}

def normalize_time(text: str) -> str:
    """将各种原始时间文本标准化为 YYYY-MM-DD 格式。
    若无法解析则返回原文本。
    """
    if not text or not text.strip():
        return ""
    t = text.strip()
    # 已有时分秒则返回原样（可能是 ISO 格式）
    if re.match(r"\d{4}-\d{2}-\d{2}[\sT]", t):
        return t[:10]

    # "May 11, 2026" / "March 10, 2026"
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", t)
    if m:
        mon = _MONTH_MAP.get(m.group(1).lower(), "01")
        return f"{m.group(3)}-{mon}-{int(m.group(2)):02d}"

    # "09 Apr, 2026Eve Air..." — day, abbreviated month, comma, year (no space after)
    m = re.match(r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[,]?\s*(\d{4})", t)
    if m:
        mon = _MONTH_MAP.get(m.group(2).lower(), "01")
        return f"{m.group(3)}-{mon}-{int(m.group(1)):02d}"

    # "12.11.2021" (DD.MM.YYYY)
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", t)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"

    # "2026.04.10" embedded in text like "试飞再升级2026.04.10沃飞长空"
    m = re.search(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", t)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # "2026/02/02"
    m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", t)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # "02/02/2026" (ambiguous, treat as MM/DD/YYYY)
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", t)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    # "2026年5月11日"
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", t)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    return ""
