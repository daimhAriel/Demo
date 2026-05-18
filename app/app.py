#!/usr/bin/env python3
"""Streamlit 管理面板 — 情报监控 Agent"""
import sys
from pathlib import Path
from datetime import datetime, time, timedelta
import uuid

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import re
import asyncio

# ── 选择器探测垃圾标题检测（用于 UI 中标记不良结果） ──
_JUNK_PATTERNS = re.compile(
    r'(阅读全文|>>|more|详情|详细|查看|点击|Read\s*More|→|»|\|)',
    re.IGNORECASE,
)
def _is_junk_title(title: str) -> bool:
    t = title.strip()
    if not t or len(t) < 6:
        return True
    return bool(_JUNK_PATTERNS.search(t))

from core.database import DatabaseHandler
from core.crawler import CrawlerManager
from core.llm import LLMProcessor
from core.pipeline import PipelineOrchestrator
from core.scheduler import CrawlScheduler
from models import SiteConfig
import config as app_config

import plotly.graph_objects as go
import plotly.express as px

st.set_page_config(
    page_title="情报监控 Agent",
    page_icon="app/favicon.svg",
    layout="wide",
)

# ── 全局 CSS（🅱️ 墨玉·松烟） ──
with open(Path(__file__).parent / "style_jadegold.css", encoding="utf-8") as f:
    _css = f.read()
st.markdown(f"<style>{_css}</style>", unsafe_allow_html=True)

# Plotly 全局模板
px.defaults.template = "plotly_white"
import plotly.graph_objects as go_lib
_template = go_lib.layout.Template(
    layout=dict(
        font=dict(family="system-ui, -apple-system, sans-serif", size=11, color="#3a5048"),
        paper_bgcolor="rgba(245,240,232,0.4)",
        plot_bgcolor="rgba(245,240,232,0.4)",
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#f5f0e8", font=dict(color="#1a3a3a", size=11), bordercolor="rgba(196,154,60,0.08)"),
        xaxis=dict(showgrid=True, gridcolor="rgba(26,58,58,0.04)", zeroline=False, color="#5a6e64"),
        yaxis=dict(showgrid=True, gridcolor="rgba(26,58,58,0.04)", zeroline=False, color="#5a6e64"),
        colorway=["#c49a3c", "#1a3a3a", "#8ab0a0", "#9b6b4a", "#5a7a6a", "#d4a880", "#6a5a4a"],
    )
)
go.layout.template = _template

db = DatabaseHandler()

# ── 调度器（Streamlit 会话级单例） ──

if "scheduler" not in st.session_state:
    st.session_state.scheduler = CrawlScheduler(db)
    _sched_enabled = db.get_app_config("scheduler_enabled")
    if _sched_enabled == "true":
        st.session_state.scheduler.start()
        st.session_state._sched_auto_started = True
    else:
        st.session_state._sched_auto_started = False


# ── 工具函数 ──

def run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── SVG 图标（data URI，1.5px 细线风格） ──

_SVG = {
    "dashboard":  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="14" width="4" height="7" rx="1"/><rect x="10" y="9" width="4" height="12" rx="1"/><rect x="17" y="5" width="4" height="16" rx="1"/></svg>',
    "globe":     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><ellipse cx="12" cy="12" rx="4" ry="10"/><path d="M2 12h20"/></svg>',
    "list":      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/></svg>',
    "gear":      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
    "folder":    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
    "trendup":   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>',
    "pencil":    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>',
    "chevdown":  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="7 13 12 18 17 13"/><polyline points="7 6 12 11 17 6"/></svg>',
    "pie":       '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21.21 15.89A10 10 0 1 1 8 2.83"/><path d="M22 12A10 10 0 0 0 12 2v10z"/></svg>',
    "plus":      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/></svg>',
    "radar":     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a10 10 0 0 1 10 10"/><path d="M12 12 16 8"/><circle cx="12" cy="12" r="3"/><path d="M2 12a10 10 0 0 1 10-10"/></svg>',
    "wrench":    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>',
    "star":      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>',
    "file":  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
    "calendar":'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
    "memo":    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="12" x2="15" y2="12"/><line x1="9" y1="16" x2="13" y2="16"/></svg>',
    "link":    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
    "clock":   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
    "play":    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>',
    "bolt":    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
    "trash":   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>',
    "satellite":'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a10 10 0 0 1 10 10"/><path d="M12 6a6 6 0 0 1 6 6"/><path d="M12 10a2 2 0 0 1 2 2"/><circle cx="12" cy="12" r="1"/><path d="M4.93 4.93a10 10 0 0 0 0 14.14"/><path d="M7.76 7.76a6 6 0 0 0 0 8.48"/><line x1="12" y1="16" x2="12" y2="22"/><line x1="9" y1="22" x2="15" y2="22"/></svg>',
    "sent":    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>',
    "inbox":   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/></svg>',
    "building":'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="2" width="16" height="20" rx="2" ry="2"/><path d="M9 22v-4h6v4"/><line x1="8" y1="6" x2="10" y2="6"/><line x1="14" y1="6" x2="16" y2="6"/><line x1="8" y1="10" x2="10" y2="10"/><line x1="14" y1="10" x2="16" y2="10"/><line x1="8" y1="14" x2="10" y2="14"/><line x1="14" y1="14" x2="16" y2="14"/></svg>',
    "newspaper":'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2zm0 0a2 2 0 0 1-2-2v-9h2"/><path d="M18 14h-8"/><path d="M15 18h-5"/><path d="M10 6h8v4h-8V6z"/></svg>',
    "law":     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v20"/><path d="M9 4H5a2 2 0 0 0-2 2v2a2 2 0 0 0 2 2h4"/><path d="M15 4h4a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2h-4"/><line x1="9" y1="12" x2="15" y2="12"/><path d="M9 16h6"/><path d="M9 20h6"/></svg>',
    "floppy":  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>',
}

def _svg_img(name, size=18, color=""):
    """生成内联 SVG HTML 标签"""
    svg = _SVG.get(name, "")
    if not svg:
        return ""
    style = f"width:{size}px;height:{size}px;flex-shrink:0;vertical-align:-3px"
    if color:
        style += f";color:{color}"
    return f'<span style="display:inline-flex;{style}">{svg}</span>'

def icon_title(name, text):
    """带图标的页面标题 HTML"""
    return f'<h1 style="display:flex;align-items:center;gap:10px;font-size:1.6em;font-weight:700;color:#1a3a3a;margin:0">{_svg_img(name, 26)}{text}</h1>'

def icon_sub(name, text):
    """带图标的 subheader HTML"""
    return f'<h3 style="display:flex;align-items:center;gap:8px;font-size:1.05em;font-weight:600;color:#1a3a3a;margin:0 0 8px 0;padding-bottom:8px;border-bottom:2px solid rgba(196,154,60,0.15)">{_svg_img(name, 18)} {text}</h3>'

def icon_button(name, text, color=""):
    """带图标的按钮/标签文字"""
    return f'{_svg_img(name, 15, color)} {text}'

_EMOJI_MAP = {
    "📊": "dashboard",
    "🌐": "globe",
    "📋": "list",
    "⚙️": "gear",
    "📂": "folder",
    "📈": "trendup",
    "📣": "trendup",
    "✏️": "pencil",
    "⬇️": "chevdown",
    "🥧": "pie",
    "✨": "star",
    "🔧": "wrench",
    "📄": "file",
    "📅": "calendar",
    "📝": "memo",
    "🔗": "link",
    "🕐": "clock",
    "▶️": "play",
    "⚡": "bolt",
    "🗑️": "trash",
}


# ── 缓存（减少页面切换时的重复查询） ──

@st.cache_data(ttl=60)
def get_cached_stats(date_from="", date_to=""):
    return db.get_stats(date_from, date_to)


@st.cache_data(ttl=60)
def get_cached_sites():
    return db.list_sites()


@st.cache_data(ttl=60)
def get_cached_companies():
    return db.get_company_list()


def site_config_form(
    prefix: str,
    defaults: SiteConfig = None,
) -> dict:
    """渲染站点配置表单，返回 dict 或 None（未提交时）"""
    with st.container():
        c1, c2 = st.columns(2)
        name = c1.text_input(
            "站点名称",
            value=defaults.name if defaults else "",
            key=f"{prefix}_name",
            placeholder="e.g. GitHub Blog",
        )
        url = c2.text_input(
            "起始 URL",
            value=defaults.url if defaults else "",
            key=f"{prefix}_url",
            placeholder="https://...",
        )
        mode = c1.selectbox(
            "爬取模式",
            ["static", "dynamic"],
            index=0 if not defaults or defaults.mode == "static" else 1,
            key=f"{prefix}_mode",
        )
        topic = c2.text_input(
            "监控主题（可选）",
            value=defaults.topic if defaults else "",
            key=f"{prefix}_topic",
            placeholder="留空则跳过 LLM 处理",
        )
        feed_url_input = c1.text_input(
            "RSS Feed URL（可选）",
            value=defaults.feed_url if defaults else "",
            key=f"{prefix}_feed_url",
            placeholder="https://.../rss.xml，留空则自动检测",
        )
        source_type = c1.selectbox(
            "源类型",
            ["company", "media", "policy"],
            index=0 if not defaults or defaults.source_type == "company"
                   else 1 if defaults.source_type == "media" else 2,
            key=f"{prefix}_source_type",
            help="company=企业官网(不过滤), media=垂类媒体(LLM过滤+公司归属), policy=政策法规",
        )

        # ── 探测按钮区 ──
        probe_key = f"{prefix}_probe"
        probe_result_key = f"{prefix}_probe_result"
        llm_probe_key = f"{prefix}_llm_probe"
        llm_result_key = f"{prefix}_llm_result"

        pc1, pc2 = st.columns(2)
        with pc1:
            if st.button("🔍 一键探测选择器", key=probe_key, use_container_width=True):
                if not url:
                    st.error("请先填写起始 URL")
                else:
                    with st.spinner("正在抓取并分析页面结构..."):
                        from app.components.selector_prober import probe_selectors
                        result = run_async(probe_selectors(url, mode))
                        st.session_state[probe_result_key] = result

        with pc2:
            if st.button("🤖 LLM 质量评估", key=llm_probe_key, use_container_width=True):
                if not url:
                    st.error("请先填写起始 URL")
                else:
                    # 从已有的探测结果中取条目
                    probe_result = st.session_state.get(probe_result_key, {})
                    candidates = probe_result.get("candidates", [])
                    all_items = []
                    for c in candidates:
                        for item in c.get("samples", []):
                            if item not in all_items:
                                all_items.append(item)

                    if not all_items:
                        st.warning("没有可评估的条目。请先点击「一键探测」获取候选条目。")
                    else:
                        with st.spinner(f"LLM 正在评估 {len(all_items)} 条候选内容的质量..."):
                            from app.components.selector_prober import probe_with_llm
                            _cfg = db.get_all_app_config()
                            llm = LLMProcessor(
                                mode=_cfg.get("llm_mode") or app_config.LLM_MODE,
                                api_key=_cfg.get("llm_api_key") or app_config.OPENAI_API_KEY,
                                base_url=_cfg.get("llm_base_url") or app_config.OPENAI_BASE_URL,
                                model=_cfg.get("llm_model") or app_config.LLM_MODEL,
                            )
                            llm_result = run_async(probe_with_llm(url, all_items, llm))
                            st.session_state[llm_result_key] = llm_result

        # 显示普通探测结果
        if st.session_state.get(probe_result_key):
            result = st.session_state[probe_result_key]
            if "error" in result:
                st.error(f"探测失败: {result['error']}")
                del st.session_state[probe_result_key]
            else:
                # 显示翻页建议
                pag = result.get("pagination", {})
                if pag and pag.get("type") not in (None, "unknown", "none"):
                    st.info(f"翻页检测: {pag.get('hint', '')}")

                # 显示候选选择器（含实际提取预览）
                if result.get("candidates"):
                    st.success(f"发现 {len(result['candidates'])} 个候选容器：")
                    for i, cand in enumerate(result["candidates"], 1):
                        q = cand.get("quality", 0)
                        total = cand.get("total_extracted", 0)
                        samples = cand.get("samples", [])
                        item_sel = cand.get("best_item_sel", "")
                        title_sel = cand.get("best_title_sel", "")
                        link_sel = cand.get("best_link_sel", "")

                        quality_icon = "✅" if q >= 3 else "⚠️"
                        with st.container(border=True):
                            cols = st.columns([3, 1])
                            cols[0].markdown(
                                f"**候选 {i}:** `{cand['selector']}` "
                                f"_{cand['type']}, {cand['item_count']}条链接_"
                            )
                            apply_key = f"{prefix}_apply_{i}"
                            if cols[1].button("📥 应用此容器", key=apply_key, use_container_width=True):
                                st.session_state[f"{prefix}_list_sel"] = cand['selector']
                                if item_sel:
                                    st.session_state[f"{prefix}_item_sel"] = item_sel
                                if title_sel:
                                    st.session_state[f"{prefix}_title_sel"] = title_sel
                                if link_sel:
                                    st.session_state[f"{prefix}_link_sel"] = link_sel
                                st.rerun()

                            if item_sel:
                                if q >= 3:
                                    st.markdown(
                                        f"{quality_icon} 推荐条目选择器: `{item_sel}` "
                                        f"（得 {q}/{total} 条有效标题）"
                                    )
                                else:
                                    st.markdown(
                                        f"{quality_icon} 推荐条目选择器: `{item_sel}` "
                                        f"（仅 {q}/{total} 条有效，可能需要调整）"
                                    )
                            else:
                                st.warning("未找到有效的条目选择器")

                            # 展示实际提取的样例
                            if samples:
                                samples_col, expand_col = st.columns([3, 1])
                                samples_col.caption(
                                    f"提取效果预览（前 {min(5, len(samples))} 条）："
                                )
                                for j, s in enumerate(samples[:5], 1):
                                    t = s['title'][:50]
                                    link_prefix = s['link'][:30]
                                    junk_flag = " [!] " if _is_junk_title(s['title']) else " ✓ "
                                    if junk_flag != " ✓ ":
                                        st.markdown(f"  {j}.{junk_flag}~~{t}~~ → `{link_prefix}`")
                                    else:
                                        st.write(f"  {j}.{junk_flag}**{t}** → `{link_prefix}`")
                                if len(samples) > 5:
                                    st.caption(f"... 还有 {len(samples)-5} 条")
                else:
                    st.warning("未发现明显的列表结构。")
                # ── RSS 自动发现兜底 ──
                rss = result.get("rss_found")
                if rss:
                    st.success(f"📡 RSS 自动发现: {rss.get('feed_title', '未知feed')}")
                    st.info(f"发现 {rss['item_count']} 条内容，来自 {rss['feed_url']}")
                    for item in rss.get("items", [])[:5]:
                        st.write(f"  - **{item['title'][:55]}** → `{item['link'][:35]}`")
                    if rss.get("item_count", 0) > 5:
                        st.caption(f"... 共 {rss['item_count']} 条")



        # 显示 LLM 质量评估结果
        if st.session_state.get(llm_result_key):
            llm_result = st.session_state[llm_result_key]
            if "error" in llm_result:
                st.error(f"LLM 评估失败: {llm_result['error']}")
                del st.session_state[llm_result_key]
            else:
                score = llm_result.get("quality_score", 0)
                good = llm_result.get("good_indices", [])
                junk = llm_result.get("junk_indices", [])
                reasoning = llm_result.get("reasoning", "")
                evaluated = llm_result.get("evaluated_items", [])

                score_icon = "✅" if score >= 0.7 else ("⚠️" if score >= 0.3 else "❌")
                st.markdown(f"**{score_icon} LLM 质量评分: {score:.0%}**  "
                            f"(有效 {len(good)} / 垃圾 {len(junk)} / 共 {len(evaluated)})")

                if reasoning:
                    with st.expander("💡 LLM 分析说明", expanded=False):
                        st.caption(reasoning)

                if evaluated:
                    st.markdown("**评估明细（前 15 条）：**")
                    for idx, item in enumerate(evaluated[:15]):
                        verdict = item.get("_llm_verdict", "unknown")
                        icon = {"good": "✅", "junk": "❌", "unknown": "❓"}.get(verdict, "❓")
                        t = item.get("title", "")[:55]
                        st.write(f"  {idx+1}. {icon} **{t}**")

        with st.expander("选择器配置", expanded=True):
            list_sel = st.text_input(
                "列表容器选择器",
                value=defaults.list_selector if defaults else "",
                key=f"{prefix}_list_sel",
                placeholder="e.g. main, div.post-list",
            )
            c3, c4 = st.columns(2)
            item_sel = c3.text_input(
                "条目选择器",
                value=defaults.item_selector if defaults else "",
                key=f"{prefix}_item_sel",
                placeholder="e.g. article, .post-item",
            )
            title_sel = c4.text_input(
                "标题选择器",
                value=defaults.title_selector if defaults else "",
                key=f"{prefix}_title_sel",
                placeholder="e.g. h2 a",
            )
            c5, c6 = st.columns(2)
            link_sel = c5.text_input(
                "链接选择器",
                value=defaults.link_selector if defaults else "",
                key=f"{prefix}_link_sel",
                placeholder="e.g. h2 a, a[href]",
            )
            time_sel = c6.text_input(
                "时间选择器（可选）",
                value=defaults.time_selector if defaults else "",
                key=f"{prefix}_time_sel",
                placeholder="e.g. time, .date",
            )
            section_sel = st.text_input(
                "🔖 新闻区块定位（可选）",
                value=defaults.section_selector if defaults else "",
                key=f"{prefix}_section_sel",
                placeholder="e.g. section.latest-news, div#breaking",
                help="如果页面有多个区域（如热门/最新/推荐），用此选择器定位目标区块。"
                     "爬虫会先缩小到该区块，再应用列表和条目选择器。",
            )

            st.caption(
                "提示：先填 URL 和模式后，可用下方「一键探测」自动推荐选择器"
            )

        with st.expander("高级设置", expanded=False):
            # ── 翻页类型 ──
            default_pag_type = "none"
            if defaults and defaults.pagination:
                default_pag_type = defaults.pagination.get("type", "none")

            pag_type = st.selectbox(
                "翻页类型",
                ["none", "query", "click"],
                index=["none", "query", "click"].index(default_pag_type),
                key=f"{prefix}_pag_type",
                help="none=单页, query=URL参数翻页, click=点击加载更多",
            )

            # 根据翻页类型显示不同参数
            if pag_type == "query":
                cc1, cc2 = st.columns(2)
                pag_param = cc1.text_input(
                    "分页参数名",
                    value=defaults.pagination.get("param", "page") if defaults and defaults.pagination else "page",
                    key=f"{prefix}_pag_param",
                    placeholder="e.g. page",
                    help="URL 查询参数名，如 ?page=2 中的 page",
                )
                pag_pattern = cc2.text_input(
                    "路径模式（可选）",
                    value=defaults.pagination.get("pattern", "") if defaults and defaults.pagination else "",
                    key=f"{prefix}_pag_pattern",
                    placeholder="e.g. /news/page/{n}/",
                    help="如果翻页在路径中而非参数中，填此模式。{n} 会被替换为页码。"
                         "如 /news/page/{n}/ → /news/page/2/",
                )
                pag_start = st.number_input(
                    "翻页起始页码",
                    min_value=1, value=defaults.pagination.get("from", 2) if defaults and defaults.pagination else 2,
                    key=f"{prefix}_pag_start",
                    help="从第几页开始翻页。初始 URL 已是第 1 页，通常设为 2 表示第 2、3、4… 页。若设为 1 则会重复请求第 1 页。",
                )
            elif pag_type == "click":
                cc1, cc2 = st.columns(2)
                pag_sel = cc1.text_input(
                    "点击按钮选择器",
                    value=defaults.pagination.get("selector", "") if defaults and defaults.pagination else "",
                    key=f"{prefix}_pag_sel",
                    placeholder="e.g. button.load-more",
                )
                pag_clicks = cc2.number_input(
                    "点击轮数",
                    min_value=1, value=defaults.pagination.get("num_clicks", 5) if defaults and defaults.pagination else 5,
                    key=f"{prefix}_pag_clicks",
                )

            c7, c8 = st.columns(2)
            max_pages = c7.number_input(
                "最大翻页数",
                min_value=1, value=defaults.max_pages if defaults else 1,
                key=f"{prefix}_max_pages",
            )
            delay_min = c8.number_input(
                "最小延迟(s)",
                min_value=0.0, value=defaults.delay_min if defaults else 0.5, step=0.1,
                key=f"{prefix}_delay_min",
            )
            c9, c10 = st.columns(2)
            delay_max = c9.number_input(
                "最大延迟(s)",
                min_value=0.0, value=defaults.delay_max if defaults else 1.5, step=0.1,
                key=f"{prefix}_delay_max",
            )
            follow_detail = c10.checkbox(
                "跟进详情页",
                value=defaults.follow_detail if defaults else False,
                key=f"{prefix}_follow_detail",
            )
            alt_content_sel = st.text_input(
                "备选内容选择器（可选）",
                value=defaults.alt_content_selector if defaults and defaults.alt_content_selector else "",
                key=f"{prefix}_alt_content_sel",
                placeholder="e.g. article .content, .post-body",
                help="当主内容选择器在详情页上不命中时，尝试此选择器。部分站点的文章使用不同模板。",
            )
            detail_title_sel = st.text_input(
                "📝 详情页标题选择器（可选）",
                value=defaults.detail_title_selector if defaults and defaults.detail_title_selector else "",
                key=f"{prefix}_detail_title_sel",
                placeholder="e.g. h1.entry-title, .post-title",
                help="详情页的标题选择器，与列表页不同时可指定。留空则使用列表页的标题选择器。",
            )
            cron_expr = st.text_input(
                "Cron 表达式",
                value=defaults.cron_expr if defaults else "0 */6 * * *",
                key=f"{prefix}_cron",
            )
        # ── 构造翻页配置字典 ──
        pagination = None
        if pag_type == "query":
            pagination = {
                "type": "query",
                "param": pag_param,
                "from": pag_start,
            }
            if pag_pattern:
                pagination["pattern"] = pag_pattern
        elif pag_type == "click":
            pagination = {
                "type": "click",
                "selector": pag_sel,
                "num_clicks": pag_clicks,
            }

        return {
            "submitted": True,
            "name": name,
            "url": url,
            "mode": mode,
            "topic": topic,
            "list_selector": list_sel,
            "item_selector": item_sel,
            "title_selector": title_sel,
            "link_selector": link_sel,
            "time_selector": time_sel,
            "section_selector": section_sel,
            "max_pages": max_pages,
            "delay_min": delay_min,
            "delay_max": delay_max,
            "follow_detail": follow_detail,
            "cron_expr": cron_expr,
            "source_type": source_type,
            "pagination": pagination,
            "alt_content_selector": alt_content_sel,
            "detail_title_selector": detail_title_sel,
            "feed_url": feed_url_input,
        }


def build_site_config(data: dict, site_id: str = "") -> SiteConfig:
    """从表单数据构建 SiteConfig"""
    return SiteConfig(
        id=site_id or f"site_{uuid.uuid4().hex[:8]}",
        name=data["name"],
        url=data["url"],
        mode=data["mode"],
        source_type=data.get("source_type", "company"),
        topic=data["topic"],
        list_selector=data["list_selector"],
        item_selector=data["item_selector"],
        title_selector=data["title_selector"],
        link_selector=data["link_selector"],
        time_selector=data["time_selector"] or None,
        section_selector=data.get("section_selector", ""),
        max_pages=data["max_pages"],
        pagination=data.get("pagination"),  # 翻页配置字典
        alt_content_selector=data.get("alt_content_selector") or None,
        detail_title_selector=data.get("detail_title_selector") or None,
        follow_detail=data["follow_detail"],
        feed_url=data.get("feed_url", ""),
        delay_min=data["delay_min"],
        delay_max=data["delay_max"],
        cron_expr=data["cron_expr"],
    )


def _count_junk_titles(items: list[dict]) -> int:
    """统计包含垃圾文本的标题数量"""
    count = 0
    for item in items:
        if _is_junk_title(item.get("title", "")):
            count += 1
    return count


def _render_validation_report(report: dict, items: list[dict]):
    """渲染选择器验证报告（含质量检查）"""
    lines = []
    lines.append("📋 **选择器验证报告**")
    for key, info in report.items():
        if key == "error":
            continue
        sel = info.get("selector", "")
        hits = info.get("hits", 0)
        status = info.get("status", "ok")
        icon = {"ok": "✅", "warn": "⚠️", "error": "❌"}.get(status, "❓")
        hits_str = f"{hits}个" if hits >= 0 else "未配置"
        lines.append(f"{icon} **{key}**: `{sel}` → {hits_str}")
    st.markdown("\n".join(lines))

    if items:
        junk_count = _count_junk_titles(items)
        quality_warnings = []
        if junk_count > len(items) * 0.3:
            quality_warnings.append(
                f"⚠️ **标题质量警告**：{junk_count}/{len(items)} 条包含垃圾文本"
                "（如「阅读全文」「>>」等），选择器可能匹配到了无效元素"
            )
        for w in quality_warnings:
            st.warning(w)

        if not quality_warnings:
            st.success(f"解析到 {len(items)} 条结果（预览前 15 条）：")
        else:
            st.info(f"解析到 {len(items)} 条结果（预览前 15 条）：")

        for i, item in enumerate(items, 1):
            title = item['title'][:60]
            href = item['link'][:50]
            st.write(f"  {i}. **{title}**")
            st.caption(f"     → {href}")
    else:
        st.error("选择器未命中任何内容，请检查选择器配置后重试。")

    # 快速诊断
    item_hits = report.get("item_selector", {}).get("hits", 0)
    if item_hits == 0:
        st.warning("诊断：条目选择器(item_selector)未命中任何元素。建议先用「LLM 智能分析」自动推荐选择器。")
    elif report.get("title_selector", {}).get("hits", 0) == 0:
        st.warning("诊断：标题选择器(title_selector)未命中。如果条目本身就是标题，可以留空。")
    elif report.get("link_selector", {}).get("hits", 0) == 0:
        st.warning("诊断：链接选择器(link_selector)未命中。如果条目本身就是链接，可以留空。")


# ── 侧边栏导航（正常点击不受干扰，程序跳转用 nav_jump） ──

PAGES = ["仪表盘", "站点管理", "情报浏览", "系统设置"]

st.sidebar.markdown(
    f'<div style="display:flex;align-items:center;gap:8px;padding:8px 0 16px 0">'
    f'{_svg_img("radar", 22)}'
    f'<span style="font-size:1.1em;font-weight:700;color:#1a3a3a">情报监控 Agent</span>'
    f'</div>',
    unsafe_allow_html=True
)

if st.session_state.get("nav_jump"):
    jump = st.session_state.pop("nav_jump")
    page = st.sidebar.radio("导航", PAGES, index=PAGES.index(jump))
else:
    page = st.sidebar.radio("导航", PAGES)

st.session_state.page = page

# ══════════════════════════════════════════════════
# 仪表盘
# ══════════════════════════════════════════════════

if page == "仪表盘":
    st.markdown(icon_title("dashboard", "仪表盘"), unsafe_allow_html=True)

    # ── 时间范围筛选（含自定义日期） ──

    today = datetime.now().date()
    sel_range = st.radio(
        "时间范围",
        ["7天", "30天", "90天", "全部", "自定义"],
        index=1, horizontal=True, label_visibility="collapsed",
        key="dash_range"
    )

    if sel_range == "自定义":
        c1, c2 = st.columns(2)
        custom_from = c1.date_input("起始日期", value=today - timedelta(days=30), key="dash_custom_from")
        custom_to = c2.date_input("结束日期", value=today, key="dash_custom_to")
        d_from = custom_from.isoformat()
        d_to = custom_to.isoformat()
    elif sel_range == "7天":
        d_from = (today - timedelta(days=7)).isoformat()
        d_to = today.isoformat()
    elif sel_range == "30天":
        d_from = (today - timedelta(days=30)).isoformat()
        d_to = today.isoformat()
    elif sel_range == "90天":
        d_from = (today - timedelta(days=90)).isoformat()
        d_to = today.isoformat()
    else:
        d_from = "2020-01-01"
        d_to = today.isoformat()

    # ── KPI 卡片（时间感知） ──

    stats = get_cached_stats(d_from, d_to)
    col1, col2, col3 = st.columns(3)
    col1.metric("站点总数", stats["total_sites"])
    col2.metric("情报总数", stats["total_items"])
    col3.metric("待推送", stats["unread_items"])

    # ── 时间感知分类分布 ──

    @st.cache_data(ttl=60, show_spinner=False)
    def get_dash_stats(date_from, date_to):
        import sqlite3
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM intel_items "
            "WHERE event_time >= ? AND event_time <= ? "
            "GROUP BY category ORDER BY cnt DESC",
            (date_from, date_to)
        ).fetchall()
        conn.close()
        return {r["category"]: r["cnt"] for r in rows}

    dash_stats = get_dash_stats(d_from, d_to)
    dash_total = sum(dash_stats.values())

    if dash_stats:
        st.markdown(icon_sub("folder", "分类概览"), unsafe_allow_html=True)

        sorted_items = sorted(dash_stats.items(), key=lambda x: -x[1])
        cats = [c for c, _ in sorted_items]
        counts = [c for _, c in sorted_items]

        # ── 趋势图 + 条形图并排 ──
        left, right = st.columns([1.2, 1.2])

        with left:
            # 堆叠面积图：分类趋势
            trend_data = db.get_category_trend(d_from, d_to)
            if trend_data:
                # 透视：{month: {cat: cnt}}
                months = []
                cat_set = set()
                pivot = {}
                for row in trend_data:
                    m = row["month"]
                    c = row["category"]
                    n = row["cnt"]
                    if m not in pivot:
                        pivot[m] = {}
                        months.append(m)
                    pivot[m][c] = n
                    cat_set.add(c)

                months_sorted = sorted(set(months))
                cat_list = sorted(cat_set, key=lambda c: -sum(pivot.get(m, {}).get(c, 0) for m in months_sorted))

                fig_area = go.Figure()
                for cat in cat_list[:7]:  # 最多 7 种颜色，不会乱
                    y_vals = [pivot.get(m, {}).get(cat, 0) for m in months_sorted]
                    fig_area.add_trace(go.Scatter(
                        x=months_sorted, y=y_vals,
                        name=cat, mode="lines",
                        stackgroup="one",
                        hovertemplate="<b>%{data.name}</b><br>%{x}: %{y} 条<extra></extra>",
                        line=dict(width=0.5),
                    ))
                fig_area.update_layout(
                    title="分类趋势",
                    height=320,
                    margin=dict(l=20, r=20, t=70, b=20),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(size=11),
                    hovermode="x unified",
                    legend=dict(orientation="h", y=1.06, font=dict(size=9.5)),
                )
                st.plotly_chart(fig_area, use_container_width=True, key="area_cat")
            else:
                st.info("该时间范围内无数据")

        with right:
            # 横向条形图：分类分布（时间感知 + 可点击）
            if dash_total > 0:
                pcts = [c / dash_total * 100 for c in counts]
                colors = px.colors.qualitative.Plotly[:len(cats)]

                fig_bar = go.Figure(go.Bar(
                    x=counts, y=cats, orientation="h",
                    text=[f"{cnt}条" for cnt in counts],
                    textposition="outside",
                    marker_color=colors,
                    hovertemplate="<b>%{y}</b><br>%{x} 条 (%{customdata:.0f}%)<extra></extra>",
                    customdata=pcts,
                    width=0.65,
                ))
                fig_bar.update_layout(
                    title="分类分布",
                    height=320,
                    margin=dict(l=10, r=100, t=70, b=10),
                    xaxis=dict(title="", showgrid=False, visible=False, automargin=True),
                    yaxis=dict(title="", showgrid=False, automargin=True),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(size=12),
                    clickmode="event+select",
                )
                event = st.plotly_chart(fig_bar, on_select="rerun", key="cat_bar2")
                if event and event.selection and event.selection.get("points"):
                    point = event.selection["points"][0]
                    clicked_cat = point.get("y")
                    if clicked_cat:
                        st.session_state["filter_category"] = clicked_cat
                        st.session_state.nav_d_from = d_from
                        st.session_state.nav_d_to = d_to
                        st.session_state.nav_jump = "情报浏览"
                        st.rerun()

        # 分类快速跳转按钮
        n_cols = min(len(cats), 3)
        pills = st.columns(n_cols)
        for i, (cat, cnt) in enumerate(sorted_items):
            with pills[i % n_cols]:
                if st.button(f"{cat} ({cnt})", key=f"dash_quick_{cat}", use_container_width=True):
                    st.session_state["filter_category"] = cat
                    st.session_state.nav_d_from = d_from
                    st.session_state.nav_d_to = d_to
                    st.session_state.nav_jump = "情报浏览"
                    st.rerun()

        # ── 公司声量折线图 ──

        st.markdown(icon_sub("trendup", "公司声量趋势"), unsafe_allow_html=True)
        comp_trend = db.get_company_trend(d_from, d_to)
        companies = get_cached_companies()
        comp_map = {c["id"]: c["name"] for c in companies}

        if comp_trend:
            # 透视
            months_c = []
            comp_pivot = {}
            for row in comp_trend:
                m = row["month"]
                cid = row["company"]
                n = row["cnt"]
                if m not in comp_pivot:
                    comp_pivot[m] = {}
                    months_c.append(m)
                # 未知公司归入"其他"
                label = comp_map.get(cid, "其他")
                comp_pivot[m][label] = comp_pivot[m].get(label, 0) + n

            months_c_sorted = sorted(set(months_c))
            # 取 top 8 公司
            comp_totals = {}
            for m in months_c_sorted:
                for label, n in comp_pivot[m].items():
                    comp_totals[label] = comp_totals.get(label, 0) + n
            top_comps = sorted(comp_totals, key=comp_totals.get, reverse=True)[:8]

            fig_line = go.Figure()
            for label in top_comps:
                y_vals = [comp_pivot.get(m, {}).get(label, 0) for m in months_c_sorted]
                fig_line.add_trace(go.Scatter(
                    x=months_c_sorted, y=y_vals,
                    name=label, mode="lines+markers",
                    hovertemplate="<b>%{data.name}</b><br>%{x}: %{y} 条<extra></extra>",
                    marker=dict(size=6),
                ))
            fig_line.update_layout(
                height=300,
                margin=dict(l=20, r=120, t=20, b=20),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(size=11),
                hovermode="x unified",
                legend=dict(orientation="v", x=1.02, y=1, font=dict(size=9.5)),
                xaxis=dict(title="", showgrid=True, gridcolor="rgba(128,128,128,0.15)"),
                yaxis=dict(title="情报条数", showgrid=True, gridcolor="rgba(128,128,128,0.15)"),
            )
            st.plotly_chart(fig_line, use_container_width=True, key="line_comp")


        # ── 最近情报（时间感知） ──

    st.subheader("最近情报")
    items = db.get_intel_items(
        limit=10,
        date_from=d_from if d_from != "2020-01-01" else None,
        date_to=d_to,
    )
    for item in items:
        with st.container():
            cols = st.columns([1, 5])
            title = item.get("clean_title") or item["raw_title"] or ""
            summary = item["summary"] or ""
            time_display = item["event_time"][:16] if item["event_time"] else ""
            cols[0].markdown(f"<span class='cat-badge'>{item['category']}</span>", unsafe_allow_html=True)
            body = f"**{title[:60]}**"
            if summary and summary != title:
                body += f"<br><span style='color:#888;font-size:0.9em'>{summary[:150]}</span>"
            body += f"<br><span style='color:#999;font-size:0.8em'>{time_display}</span>"
            cols[1].markdown(body, unsafe_allow_html=True)
            with st.expander("查看详情"):
                st.markdown(f'{_svg_img("file", 14)} 原始标题: {item["raw_title"]}', unsafe_allow_html=True)
                if summary and summary != title:
                    st.markdown(f'{_svg_img("memo", 14)} 摘要: {summary}', unsafe_allow_html=True)
                st.markdown(f'{_svg_img("link", 14)} {item["source_url"]}', unsafe_allow_html=True)
                if item["event_time"]:
                    st.markdown(f'{_svg_img("calendar", 14)} 事件时间: {item["event_time"]}', unsafe_allow_html=True)
                st.markdown(f'{_svg_img("clock", 14)} 抓取时间: {item["crawled_at"]}', unsafe_allow_html=True)
        st.divider()




# ══════════════════════════════════════════════════
# 🌐 站点管理
# ══════════════════════════════════════════════════

elif page == "站点管理":
    st.markdown(icon_title("globe", "站点管理"), unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["站点列表", "新增站点"])

    # ── Tab 1: 站点列表 ──

    with tab1:
        sites = get_cached_sites()
        if not sites:
            st.info("还没有站点配置。请在「新增站点」页添加。")
        else:
            for idx, site in enumerate(sites):
                with st.container():
                    stype_icon = {"company": "building", "media": "newspaper", "policy": "law"}
                    stype_label = {"company": "企业", "media": "媒体", "policy": "政策"}
                    status = "🟢" if site.enabled else "🔴"
                    src_type = getattr(site, "source_type", "company") or "company"
                    src_icon = stype_icon.get(src_type, "building")

                    cols = st.columns([3, 1.0, 0.6, 1.0, 1.3, 1.3, 1.5])
                    cols[0].write(f"{status} {site.name}")
                    cols[1].markdown(f'{_svg_img(src_icon, 14, "#8ab0a0")} {stype_label.get(src_type, src_type)}', unsafe_allow_html=True)
                    cols[2].write(site.mode)
                    cols[3].write(site.topic or "")

                    # 编辑按钮
                    edit_key = f"edit_{site.id}"
                    if cols[4].button("✏️ 编辑", key=edit_key):
                        st.session_state["editing_site"] = site.id

                    # 删除按钮
                    del_key = f"del_{site.id}"
                    if cols[5].button("🗑️ 删除", key=del_key):
                        st.session_state["deleting_site"] = site.id

                    # 执行爬取（点击后标记，在下方执行）
                    run_key = f"run_{site.id}"
                    if cols[6].button("⚡ 执行", key=run_key):
                        st.session_state["running_site"] = site.id

                # ── 编辑弹窗 ──
                if st.session_state.get("editing_site") == site.id:
                    with st.expander(f"编辑: {site.name}", expanded=True):
                        form_data = site_config_form(
                            prefix=f"edit_{site.id}",
                            defaults=site,
                        )
                        # ── 预览修改 ──
                        edit_preview_key = f"preview_edit_{site.id}"
                        if st.button("🔍 预览解析结果", key=edit_preview_key):
                            if not form_data["url"]:
                                st.error("请先填写起始 URL")
                            else:
                                with st.spinner("正在抓取页面..."):
                                    edit_config = build_site_config(form_data, site_id=site.id)
                                    from app.components.preview import preview_with_report
                                    result = run_async(preview_with_report(form_data["url"], edit_config))
                                if "error" in result.get("report", {}):
                                    st.error(f"抓取失败: {result['report']['error']}")
                                else:
                                    _render_validation_report(result.get("report", {}), result.get("items", []))

                        c1, c2, c3 = st.columns([1, 1, 4])
                        if c1.button("保存修改", key=f"save_{site.id}", type="primary"):
                            updated = build_site_config(form_data, site_id=site.id)
                            # 验证选择器
                            from app.components.preview import preview_with_report
                            val_result = run_async(preview_with_report(form_data["url"], updated))
                            if "error" in val_result.get("report", {}):
                                st.error(f"抓取验证失败: {val_result['report']['error']}")
                            else:
                                item_count = val_result.get("report", {}).get("item_selector", {}).get("hits", 0)
                                if item_count == 0:
                                    st.error("选择器未命中任何内容，禁止保存。请先点「预览解析结果」调整选择器。")
                                else:
                                    items = val_result.get("items", [])
                                    junk = _count_junk_titles(items)
                                    if junk > len(items) * 0.3:
                                        st.error(f"标题质量检查失败：{junk}/{len(items)} 条包含垃圾文本。请先点「预览解析结果」查看并调整选择器。")
                                    else:
                                        updated.enabled = site.enabled
                                        updated.last_seen_url = site.last_seen_url
                                        updated.last_crawled_at = site.last_crawled_at
                                        db.save_site(updated)
                                        st.cache_data.clear()
                                        st.toast(f"站点「{updated.name}」已更新！(验证: {len(items)} 条)")
                                        del st.session_state["editing_site"]
                                        st.rerun()
                        if c2.button("取消", key=f"cancel_edit_{site.id}"):
                            del st.session_state["editing_site"]
                            st.rerun()

                # ── 删除确认 ──
                if st.session_state.get("deleting_site") == site.id:
                    with st.container():
                        st.warning(f"确定删除站点「{site.name}」？关联的情报不会被删除。")
                        c1, c2 = st.columns([1, 5])
                        if c1.button("确认删除", key=f"confirm_del_{site.id}"):
                            db.delete_site(site.id)
                            st.cache_data.clear()
                            st.toast(f"已删除: {site.name}")
                            del st.session_state["deleting_site"]
                            st.rerun()
                        if c2.button("取消", key=f"cancel_del_{site.id}"):
                            del st.session_state["deleting_site"]
                            st.rerun()

                # ── 执行爬取（来自行内按钮触发） ──
                if st.session_state.get("running_site") == site.id:
                    with st.spinner(f"正在爬取 {site.name}..."):
                        crawler = CrawlerManager()
                        # 从数据库读取 LLM 配置（优先），环境变量兜底
                        _cfg = db.get_all_app_config()
                        llm = LLMProcessor(
                            mode=_cfg.get("llm_mode") or app_config.LLM_MODE,
                            api_key=_cfg.get("llm_api_key") or app_config.OPENAI_API_KEY,
                            base_url=_cfg.get("llm_base_url") or app_config.OPENAI_BASE_URL,
                            model=_cfg.get("llm_model") or app_config.LLM_MODEL,
                        )
                        orch = PipelineOrchestrator(db, crawler, llm)
                        try:
                            count = run_async(orch.run_site(site))
                            st.success(f"爬取完成！新增 {count} 条情报")
                        except Exception as e:
                            st.error(f"爬取失败: {e}")
                        finally:
                            run_async(crawler.close())
                    del st.session_state["running_site"]

                st.divider()




    # ── Tab 2: 新增站点 ──

    with tab2:
        st.markdown(icon_sub("star", "新增监控站点"), unsafe_allow_html=True)

        form_data = site_config_form(prefix="new")

        # ── 预览解析 ──
        preview_key = "preview_new"
        if st.button("预览解析结果", key=preview_key):
            if not form_data["url"]:
                st.error("请先填写起始 URL")
            else:
                with st.spinner("正在抓取页面..."):
                    config = build_site_config(form_data)
                    from app.components.preview import preview_with_report
                    result = run_async(preview_with_report(form_data["url"], config))
                if "error" in result.get("report", {}):
                    st.error(f"抓取失败: {result['report']['error']}")
                else:
                    report = result.get("report", {})
                    items = result.get("items", [])
                    _render_validation_report(report, items)

        st.divider()




        # ── 保存按钮（自动验证） ──
        if st.button("保存站点", type="primary", key="save_new_site"):
            missing = []
            if not form_data["name"]:
                missing.append("站点名称")
            if not form_data["url"]:
                missing.append("起始 URL")
            if missing:
                st.error(f"请填写: {', '.join(missing)}")
            else:
                # 自动验证选择器
                config = build_site_config(form_data)
                from app.components.preview import preview_with_report
                val_result = run_async(preview_with_report(form_data["url"], config))
                if "error" in val_result.get("report", {}):
                    st.error(f"抓取验证失败: {val_result['report']['error']}，请检查 URL 是否正确")
                else:
                    report = val_result.get("report", {})
                    item_count = report.get("item_selector", {}).get("hits", 0)
                    if item_count == 0:
                        st.error("选择器未命中任何内容，禁止保存。请先点「预览解析结果」调整选择器。")
                    else:
                        items = val_result.get("items", [])
                        junk = _count_junk_titles(items)
                        if junk > len(items) * 0.3:
                            st.error(f"标题质量检查失败：{junk}/{len(items)} 条包含垃圾文本。请先点「预览解析结果」查看并调整选择器。")
                        else:
                            site = build_site_config(form_data)
                            db.save_site(site)
                            st.cache_data.clear()
                            st.toast(f"站点「{site.name}」已保存！(验证: {len(items)} 条)")
                            st.rerun()


# ══════════════════════════════════════════════════
# 📋 情报浏览
# ══════════════════════════════════════════════════

elif page == "情报浏览":
    st.markdown(icon_title("list", "情报浏览"), unsafe_allow_html=True)

    # 检查是否有来自仪表盘的分类筛选
    default_category = st.session_state.pop("filter_category", "")

    # ── 四轴筛选（站点 + 公司 + 分类 + 日期） ──
    col1, col2, col3, col4 = st.columns(4)

    sites = get_cached_sites()
    companies = get_cached_companies()
    comp_map = {c["id"]: c["name"] for c in companies}

    # 构建 site_id → company_id 映射（company 类型站点专用）
    site_to_company = {}
    site_type_map = {}
    for s in sites:
        site_type_map[s.id] = s.source_type
    for c in companies:
        sid = c.get("site_id")
        if sid:
            site_to_company[sid] = c["id"]

    # 1. 筛选站点
    site_options = {s.id: s.name for s in sites}
    sel_site = col1.selectbox(
        "筛选站点",
        [""] + list(site_options.keys()),
        format_func=lambda x: "▼ 全部网站" if x == "" else site_options.get(x, x),
        key="view_site_filter",
    )
    actual_site_id = sel_site or None

    # 2. 筛选公司 — 根据站点类型决定行为
    locked_company = None
    if actual_site_id and site_type_map.get(actual_site_id) == "company":
        locked_company = site_to_company.get(actual_site_id)

    comp_options = {c["id"]: c["name"] for c in companies}
    COMPANY_OTHER = "__other__"

    if locked_company:
        # company 站点 → 锁定到对应公司，禁止手动切换
        col2.selectbox(
            "筛选公司",
            [locked_company],
            format_func=lambda x: comp_options.get(x, x),
            disabled=True,
            key="view_company_filter_locked",
        )
        sel_company = locked_company
    else:
        # media/policy 站点或未选站点 → 自由下拉（含「其他」）
        comp_keys = [""] + list(comp_options.keys()) + [COMPANY_OTHER]
        sel_company = col2.selectbox(
            "筛选公司",
            comp_keys,
            format_func=lambda x: "▼ 全部公司" if x == "" else ("其他（不在公司列表中）" if x == COMPANY_OTHER else comp_options.get(x, x)),
            key="view_company_filter",
        )

    # 3. 分类
    stats = get_cached_stats()
    cat_options = {c: c for c in sorted(stats["by_category"].keys())}
    cat_keys = [""] + list(cat_options.keys())
    default_cat_index = 0
    if default_category and default_category in cat_options:
        default_cat_index = cat_keys.index(default_category)
    sel_cat = col3.selectbox(
        "分类",
        cat_keys,
        format_func=lambda x: "▼ 全部分类" if x == "" else cat_options.get(x, x),
        index=default_cat_index,
        key="view_cat_filter",
    )

    # 4. 日期范围（支持从仪表盘跳转时自动填入）
    today = datetime.now().date()
    nav_d_from = st.session_state.pop("nav_d_from", None)
    nav_d_to = st.session_state.pop("nav_d_to", None)
    if nav_d_from:
        try:
            parsed = datetime.fromisoformat(nav_d_from).date()
            st.session_state["view_date_from"] = parsed
        except Exception:
            pass
    if nav_d_to:
        try:
            parsed = datetime.fromisoformat(nav_d_to).date()
            st.session_state["view_date_to"] = parsed
        except Exception:
            pass
    date_from, date_to = col4.columns(2)
    sel_date_from = date_from.date_input(
        "起始",
        value=None,
        key="view_date_from",
        help="事件日期起始，留空不限",
    )
    sel_date_to = date_to.date_input(
        "结束",
        value=None,
        key="view_date_to",
        help="事件日期结束，留空不限",
    )

    # ── 分页：默认加载 50 条，支持「加载更多」 ──

    # 检测筛选条件是否变更（条件变了就重置到 50 条）
    _filter_key = f"{actual_site_id or ''}|{sel_company or ''}|{sel_cat or ''}|{sel_date_from or ''}|{sel_date_to or ''}"
    if st.session_state.get("_intel_filter_key") != _filter_key:
        st.session_state._intel_filter_key = _filter_key
        st.session_state.view_limit = 20
    if "view_limit" not in st.session_state:
        st.session_state.view_limit = 20

    items = db.get_intel_items(
        limit=st.session_state.view_limit,
        site_id=actual_site_id,
        category=sel_cat or None,
        company=sel_company or None,
        date_from=sel_date_from.isoformat() if sel_date_from else None,
        date_to=sel_date_to.isoformat() if sel_date_to else None,
    )

    st.caption(f"当前显示 {len(items)} 条结果")

    # ── 如果选中了分类，显示公司分布饼图 ──
    if sel_cat:
        st.markdown(f'{_svg_img("pie", 18, "#c49a3c")} <b>「{sel_cat}」公司分布</b>', unsafe_allow_html=True)

        # 时间感知：使用情报浏览页面的日期筛选
        d_f = sel_date_from.isoformat() if sel_date_from else ""
        d_t = sel_date_to.isoformat() if sel_date_to else ""
        company_counts = db.get_items_by_category_grouped_by_company(sel_cat, d_f, d_t)
        if company_counts:
            # 将不在公司列表中的 ID 归入「其他」
            grouped = {}
            other_total = 0
            for cid, cnt in company_counts.items():
                if cid in comp_map:
                    name = comp_map[cid]
                    grouped[name] = grouped.get(name, 0) + cnt
                else:
                    other_total += cnt
            if other_total > 0:
                grouped["其他"] = other_total

            comp_labels = list(grouped.keys())
            comp_values = list(grouped.values())
            total = sum(comp_values)
            # < 3% 的小扇区不显示文字标签，用 pull 拉开
            pulls = [0.08 if v / total < 0.03 else 0 for v in comp_values]
            text_labels = [l if v / total >= 0.03 else "" for l, v in zip(comp_labels, comp_values)]
            fig_pie = go.Figure(data=[go.Pie(
                labels=comp_labels,
                values=comp_values,
                hovertemplate="<b>%{label}</b><br>%{value} 条<br>占比: %{percent}",
                text=text_labels,
                textinfo="text+percent",
                textposition="auto",
                insidetextorientation="radial",
                pull=pulls,
                showlegend=True,
            )])
            fig_pie.update_layout(
                height=320,
                margin=dict(t=30, b=30, l=30, r=30),
                paper_bgcolor="rgba(0,0,0,0)",
                font=dict(size=11),
                legend=dict(font=dict(size=10)),
            )
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.caption("该分类下暂无公司归属数据")
        st.divider()

    # 分类选项（编辑用）
    ALL_CATS = [
        "商业化情况", "战略目标", "产品与工程",
        "适航情况", "产能规划", "股权与资本", "政策法规", "其他",
    ]

    for item in items:
        fp = item["fingerprint"]
        edit_key = f"edit_intel_{fp}"

        # ── 编辑模式 ──
        if st.session_state.get(edit_key, False):
            with st.container():
                st.markdown(f'<span style="display:inline-flex;align-items:center;gap:6px;font-weight:600">'
                    f'{_svg_img("pencil", 14)} 编辑情报</span>', unsafe_allow_html=True)
                c_title = item.get("clean_title") or item["raw_title"] or ""
                new_clean = st.text_input("清洗标题 (clean_title)", value=c_title, key=f"ec_t_{fp}")
                new_raw = st.text_input("原始标题 (raw_title)", value=item["raw_title"], key=f"er_t_{fp}")
                new_summary = st.text_input("摘要", value=item["summary"], key=f"es_{fp}")
                c1, c2, c3 = st.columns(3)
                new_cat = c1.selectbox(
                    "分类", ALL_CATS,
                    index=ALL_CATS.index(item["category"]) if item["category"] in ALL_CATS else len(ALL_CATS)-1,
                    key=f"ec_{fp}",
                )
                # 编辑公司归属：未知公司显示为「▼ 无」
                company_val = item.get("company", "") or ""
                if company_val not in [""] + list(comp_options.keys()):
                    company_val = ""  # 未识别公司自动回退空白
                comp_edit_options = [""] + list(comp_options.keys())
                new_company = c2.selectbox(
                    "公司归属",
                    comp_edit_options,
                    format_func=lambda x: "▼ 无" if x == "" else comp_options.get(x, x),
                    index=comp_edit_options.index(company_val) if company_val in comp_edit_options else 0,
                    key=f"eco_{fp}",
                )
                new_time = c3.text_input("事件时间", value=item.get("event_time", "") or "", key=f"et_{fp}")
                bc1, bc2, bc3 = st.columns([1, 1, 8])
                if bc1.button("保存", key=f"save_{fp}"):
                    db.update_item_fields(
                        fp,
                        clean_title=new_clean,
                        raw_title=new_raw,
                        summary=new_summary,
                        category=new_cat,
                        company=new_company or "",
                        event_time=new_time,
                    )
                    st.session_state[edit_key] = False
                    st.success("已保存")
                    st.rerun()
                if bc2.button("取消", key=f"cancel_{fp}"):
                    st.session_state[edit_key] = False
                    st.rerun()
            st.divider()
            continue

        # ── 只读模式 ──
        with st.container():
            cols = st.columns([1.2, 4.5, 1.5, 1.3])
            cols[0].markdown(f"<span class='cat-badge'>{item['category']}</span>", unsafe_allow_html=True)
            title = item.get("clean_title") or item["raw_title"] or item["summary"][:60]
            summary = item["summary"]
            if summary == item["raw_title"]:
                cols[1].markdown(f"**{title[:80]}**")
            else:
                cols[1].markdown(
                    f"**{title[:60]}**\n\n"
                    f"<span style='color:#888;font-size:0.85em'>{summary[:150]}</span>",
                    unsafe_allow_html=True,
                )
            time_display = item["event_time"][:16] if item["event_time"] else ""
            cols[2].caption(time_display)
            if cols[3].button("✏️ 编辑", key=f"edit_btn_{fp}", help="编辑"):
                st.session_state[edit_key] = True
                st.rerun()
            with st.expander("查看详情"):
                st.markdown(f'{_svg_img("link", 14)} {item["source_url"]}', unsafe_allow_html=True)
                if item["event_time"]:
                    st.markdown(f'{_svg_img("calendar", 14)} 事件时间: {item["event_time"]}', unsafe_allow_html=True)
                st.markdown(f'{_svg_img("clock", 14)} 抓取: {item["crawled_at"]}', unsafe_allow_html=True)
                st.markdown(f'{_svg_img("file", 14)} 原始标题: {item["raw_title"]}', unsafe_allow_html=True)
                if item["summary"] != item["raw_title"]:
                    st.markdown(f'{_svg_img("memo", 14)} 摘要: {item["summary"]}', unsafe_allow_html=True)
                if item.get("company"):
                    cn = comp_map.get(item["company"], item["company"])
                    st.write(f"🏷️ 关联公司: {cn}")
        st.divider()

    # ── 加载更多按钮 ──
    if len(items) >= st.session_state.view_limit:
        if st.button("加载更多（+50条）", use_container_width=True):
            st.session_state.view_limit += 50
            st.rerun()

    # 显示当前加载量
    st.caption(f"当前显示 {len(items)} 条")



# ══════════════════════════════════════════════════
# 系统设置
# ══════════════════════════════════════════════════

else:
    st.markdown(icon_title("gear", "系统设置"), unsafe_allow_html=True)

    st.subheader("LLM 配置")
    st.caption("配置后保存到数据库，爬取时自动读取。留空 = 跳过 LLM 处理。")

    # 从数据库读取已保存的 LLM 配置（优先），否则从环境变量读取默认值
    _saved = db.get_all_app_config()
    _llm_mode = _saved.get("llm_mode", app_config.LLM_MODE)
    _llm_api_key = _saved.get("llm_api_key", app_config.OPENAI_API_KEY)
    _llm_base_url = _saved.get("llm_base_url", app_config.OPENAI_BASE_URL)
    _llm_model = _saved.get("llm_model", app_config.LLM_MODEL)
    _llm_ollama_url = _saved.get("llm_ollama_url", app_config.OLLAMA_BASE_URL)

    c1, c2 = st.columns([1, 3])
    llm_mode = c1.selectbox(
        "LLM 模式",
        ["disabled", "openai", "ollama"],
        index=["disabled", "openai", "ollama"].index(_llm_mode)
        if _llm_mode in ["disabled", "openai", "ollama"] else 1,
    )
    llm_api_key = c2.text_input(
        "API Key",
        value=_llm_api_key,
        type="password",
        placeholder="sk-... 或留空",
        disabled=(llm_mode == "disabled"),
    )
    llm_base_url = st.text_input(
        "API Base URL",
        value=_llm_base_url,
        placeholder="https://api.openai.com/v1",
        disabled=(llm_mode in ["disabled", "ollama"]),
    )
    llm_model = st.text_input(
        "模型名称",
        value=_llm_model,
        placeholder="gpt-4o-mini",
        disabled=(llm_mode == "disabled"),
    )
    llm_ollama_url = st.text_input(
        "Ollama Base URL",
        value=_llm_ollama_url,
        placeholder="http://localhost:11434/v1",
        disabled=(llm_mode != "ollama"),
    )

    if st.button("保存 LLM 配置"):
        db.set_app_config("llm_mode", llm_mode)
        db.set_app_config("llm_api_key", llm_api_key)
        db.set_app_config("llm_base_url", llm_base_url)
        db.set_app_config("llm_model", llm_model)
        db.set_app_config("llm_ollama_url", llm_ollama_url)
        # 同步更新 config 模块
        app_config.LLM_MODE = llm_mode
        app_config.OPENAI_API_KEY = llm_api_key
        app_config.OPENAI_BASE_URL = llm_base_url
        app_config.LLM_MODEL = llm_model
        app_config.OLLAMA_BASE_URL = llm_ollama_url
        st.success("LLM 配置已保存到数据库！")

    st.divider()




    st.subheader("数据库")
    st.code("data/intel.db")
    stats = get_cached_stats()
    st.write(
        f"占用条目: {stats['total_items']} 条情报 + "
        f"{stats['total_sites']} 个站点"
    )

    # 清空全部情报
    if st.button("清空全部情报数据", type="secondary"):
        st.warning("此操作将删除所有爬取的情报，但保留站点配置。")
        c1, c2 = st.columns([1, 5])
        if c1.button("确认清空", key="confirm_clear"):
            count = db.clear_all_intel()
            st.success(f"已清空 {count} 条情报！")
            st.rerun()
        if c2.button("取消", key="cancel_clear"):
            st.rerun()

    st.divider()


    # ── ⏰ 定时调度 ──

    st.subheader("⏰ 定时调度")
    st.caption("后台线程自动爬取全部已启用站点。状态持久化到数据库，重启面板后自动恢复。")

    scheduler: CrawlScheduler = st.session_state.scheduler
    sched_enabled = db.get_app_config("scheduler_enabled") == "true"
    sched_cron = db.get_app_config("scheduler_cron") or "0 0 * * 1"

    # 启用/禁用 开关
    toggle = st.toggle("启用定时爬取", value=sched_enabled)

    # ── 直观调度设置 ──

    def _parse_cron_parts(cron_expr: str):
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            return "0", "0", "*", "*", "0"
        return parts[0], parts[1], parts[2], parts[3], parts[4]

    def _cron_to_friendly(cron_expr: str):
        """解析 cron → (频率str, 时间, 星期索引, 日数字)"""
        c_min, c_hour, c_day, c_month, c_dow = _parse_cron_parts(cron_expr)
        try:
            if c_min.startswith("*/"):
                return "每30分钟", time(0, 0), 0, 1
            if c_hour.startswith("*/"):
                interval = c_hour.split("*/")[1]
                return f"每{interval}小时", time(0, 0), 0, 1
            h, m = int(c_hour or 0), int(c_min or 0)
            t = time(h, m)
            if c_day == "*" and c_dow in ("*", "?"):
                return "每天", t, 0, 1
            if c_day == "*" and c_dow != "*":
                return "每周", t, int(c_dow), 1
            return "每月", t, 0, min(int(c_day), 28)
        except (ValueError, TypeError):
            return "每6小时", time(0, 0), 0, 1

    def _friendly_to_cron(freq, time_val, dow_val, day_val):
        m = time_val.minute
        h = time_val.hour
        if freq == "每30分钟":
            return "*/30 * * * *"
        if freq == "每小时":
            return "0 * * * *"
        if freq == "每2小时":
            return "0 */2 * * *"
        if freq == "每6小时":
            return "0 */6 * * *"
        if freq == "每天":
            return f"{m} {h} * * *"
        if freq == "每周":
            return f"{m} {h} * * {dow_val}"
        if freq == "每月":
            return f"{m} {h} {day_val} * *"
        return "0 0 * * 0"

    freq, base_time, base_dow, base_day = _cron_to_friendly(sched_cron)
    freq_options = ["每30分钟", "每小时", "每2小时", "每6小时", "每天", "每周", "每月"]
    freq_idx = freq_options.index(freq) if freq in freq_options else 3
    freq = st.selectbox("执行频率", freq_options, index=freq_idx, key="sched_freq")

    is_interval = freq in ("每30分钟", "每小时", "每2小时", "每6小时")

    tcol1, tcol2 = st.columns([1, 1])
    with tcol1:
        if not is_interval:
            time_val = st.time_input("执行时间", value=base_time, key="sched_time")
        else:
            time_val = time(0, 0)

    with tcol2:
        dow_val = base_dow
        day_val = base_day
        if freq == "每周":
            dow_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            dow_idx = base_dow if 0 <= base_dow <= 6 else 0
            dow_name = st.selectbox("星期", dow_names, index=dow_idx, key="sched_dow")
            dow_val = dow_names.index(dow_name)
        elif freq == "每月":
            day_val = st.number_input("每月的第几天", min_value=1, max_value=28,
                                     value=base_day, key="sched_day")
        else:
            day_val = 1

    # 合成 cron
    cron_input = _friendly_to_cron(freq, time_val, dow_val, day_val)
    st.caption(f"📌 对应 Cron 表达式：`{cron_input}`")

    # 状态列
    col_a, col_b, col_c = st.columns(3)
    status_icon = "🟢" if scheduler.running else "🔴"
    col_a.metric("调度器状态", f"{status_icon} {'运行中' if scheduler.running else '已暂停'}")
    col_b.metric("下次执行", scheduler.get_next_run_time())
    col_c.metric("监控站点", get_cached_stats()["total_sites"])

    # 操作按钮
    act_cols = st.columns([1, 1, 5])
    with act_cols[0]:
        if scheduler.running:
            if st.button("⏹ 暂停调度", width="stretch"):
                scheduler.stop()
                db.set_app_config("scheduler_enabled", "false")
                st.rerun()
        else:
            if st.button("启动调度", width="stretch"):
                scheduler.update_cron(cron_input)
                scheduler.start()
                db.set_app_config("scheduler_enabled", "true")
                st.rerun()

    with act_cols[1]:
        if st.button("立即执行", width="stretch"):
            with st.spinner("正在爬取全部站点..."):
                _cfg = db.get_all_app_config()
                llm = LLMProcessor(
                    mode=_cfg.get("llm_mode", "openai"),
                    api_key=_cfg.get("llm_api_key", ""),
                    base_url=_cfg.get("llm_base_url", ""),
                    model=_cfg.get("llm_model", "gpt-4o-mini"),
                )
                pipeline = PipelineOrchestrator(
                    db, CrawlerManager(), llm
                )
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    results = loop.run_until_complete(pipeline.run_all())
                    total = sum(results.values())
                    st.success(f"爬取完成！共 {total} 条新情报")
                    # 保存执行记录
                    now = datetime.now().strftime("%Y-%m-%d %H:%M")
                    db.set_app_config("last_scheduler_run_time", now)
                    db.set_app_config("last_scheduler_new_count", str(total))
                    db.set_app_config("last_scheduler_status", "success")
                    db.set_app_config("last_scheduler_error", "")
                except Exception as e:
                    st.error(f"爬取出错: {e}")
                    now = datetime.now().strftime("%Y-%m-%d %H:%M")
                    db.set_app_config("last_scheduler_run_time", now)
                    db.set_app_config("last_scheduler_new_count", "0")
                    db.set_app_config("last_scheduler_status", "failed")
                    db.set_app_config("last_scheduler_error", str(e))
                finally:
                    loop.close()
            st.rerun()

    # 同步 toggle 状态变更
    if toggle and not scheduler.running:
        scheduler.update_cron(cron_input)
        scheduler.start()
        db.set_app_config("scheduler_enabled", "true")
        st.rerun()
    elif not toggle and scheduler.running:
        scheduler.stop()
        db.set_app_config("scheduler_enabled", "false")
        st.rerun()
    elif toggle and scheduler.running and cron_input != scheduler.get_cron_expr():
        scheduler.update_cron(cron_input)
        # 如果新时间已过（比如从 00:00 改为 10:00 时已是 10:05），
        # 触发一次立即执行
        next_time = scheduler.get_next_run_time()
        if next_time and next_time != "—":
            try:
                from datetime import datetime
                nxt = datetime.strptime(next_time, "%Y-%m-%d %H:%M")
                now = datetime.now()
                # 如果下次执行距现在超过 1 小时，说明今天的窗口已错过
                if (nxt - now).total_seconds() > 3600:
                    with st.spinner("检测到时间已过，立即执行一次..."):
                        _cfg = db.get_all_app_config()
                        llm = LLMProcessor(
                            mode=_cfg.get("llm_mode", "openai"),
                            api_key=_cfg.get("llm_api_key", ""),
                            base_url=_cfg.get("llm_base_url", ""),
                            model=_cfg.get("llm_model", "gpt-4o-mini"),
                        )
                        pipeline = PipelineOrchestrator(
                            db, CrawlerManager(), llm
                        )
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            results = loop.run_until_complete(pipeline.run_all())
                            total = sum(results.values())
                            st.success(f"定时时间已过，已自动触发爬取！共 {total} 条新情报")
                        except Exception as e:
                            st.error(f"自动触发爬取出错: {e}")
                        finally:
                            loop.close()
            except Exception:
                pass
        st.rerun()

    # ── 最近执行记录 ──

    st.markdown("**📋 最近一次执行**")
    last_time = db.get_app_config("last_scheduler_run_time")
    last_count = db.get_app_config("last_scheduler_new_count")
    last_status = db.get_app_config("last_scheduler_status")
    last_error = db.get_app_config("last_scheduler_error")

    if last_time:
        status_emoji = "✅" if last_status == "success" else "❌"
        ecols = st.columns([1, 1, 1])
        ecols[0].metric("执行时间", last_time)
        ecols[1].metric("执行状态", f"{status_emoji} {'成功' if last_status == 'success' else '失败'}")
        ecols[2].metric("新增情报", f"{last_count} 条")
        if last_status == "failed" and last_error:
            st.error(f"错误信息: {last_error}")
    else:
        st.info("暂无执行记录。调度器触发后会自动记录。")

    st.divider()

    # ── 🔔 推送通知配置 ──

    st.subheader("推送通知")
    st.caption("新情报入库时自动推送。支持飞书群机器人和邮件。")

    from core.notifier import Notifier
    notif = Notifier(db)
    notif_cfg = notif.get_config()
    notif_enabled = st.toggle("启用推送通知", value=notif_cfg.get("enabled", False))

    notif_cfg["enabled"] = notif_enabled

    if notif_enabled:
        tabs_n = st.tabs(["飞书群机器人", "邮件 SMTP"])

        # ── 飞书 ──
        with tabs_n[0]:
            fs = notif_cfg["channels"]["feishu"]
            fs["enabled"] = st.checkbox("启用飞书推送", value=fs.get("enabled", True), key="notif_fs_enabled")
            fs["webhook_url"] = st.text_input(
                "Webhook 地址",
                value=fs.get("webhook_url", ""),
                placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx",
                key="notif_fs_url",
            )
            st.caption("在飞书群设置中添加群机器人，复制 Webhook URL 填入上方。")

        # ── 邮件 ──
        with tabs_n[1]:
            em = notif_cfg["channels"]["email"]
            em["enabled"] = st.checkbox("启用邮件推送", value=em.get("enabled", False), key="notif_em_enabled")
            c1, c2 = st.columns(2)
            em["smtp_server"] = c1.text_input("SMTP 服务器", value=em.get("smtp_server", ""), key="notif_em_server")
            em["smtp_port"] = c2.number_input("端口", value=em.get("smtp_port", 587), min_value=1, max_value=65535, key="notif_em_port")
            em["smtp_user"] = st.text_input("用户名", value=em.get("smtp_user", ""), key="notif_em_user")
            em["smtp_password"] = st.text_input(
                "密码", value=em.get("smtp_password", ""), type="password", key="notif_em_pass"
            )
            c1, c2 = st.columns(2)
            em["from_addr"] = c1.text_input("发件地址", value=em.get("from_addr", ""), key="notif_em_from")
            em["to_addrs"] = c2.text_input("收件地址（逗号分隔）", value=em.get("to_addrs", ""), key="notif_em_to")
            em["use_tls"] = st.checkbox("使用 TLS", value=em.get("use_tls", True), key="notif_em_tls")

        # ── 保存 ──
        cols_save = st.columns([1, 1, 4])
        if cols_save[0].button("保存通知设置", type="primary", key="notif_save"):
            notif.save_config(notif_cfg)
            st.success("通知配置已保存！")
            st.rerun()

        if cols_save[1].button("发送测试通知", key="notif_test"):
            with st.spinner("正在发送测试通知..."):
                results = notif.send_test()
            for ch, res in results.items():
                if ch == "status":
                    continue
                if res == "ok":
                    st.success(f"✅ {ch} 测试成功！")
                else:
                    st.error(f"❌ {ch} 发送失败: {res}")

    st.divider()

    st.subheader("🏷️ 公司列表管理")
    st.caption("用于垂类媒体文章的公司归属判断。新增/修改后需保存。")

    companies = db.get_company_list()
    edited = False

    # 公司列表编辑器用的站点映射下拉
    all_sites = db.list_sites()
    site_opts_for_comp = {s.id: s.name for s in all_sites}

    # 公司列表编辑器
    for i, c in enumerate(companies):
        with st.container():
            cols = st.columns([1.5, 3, 2.5, 3, 0.5])
            cid = cols[0].text_input(f"公司ID", value=c["id"], key=f"comp_id_{i}")
            cname = cols[1].text_input(f"显示名称", value=c["name"], key=f"comp_name_{i}")
            # 站点映射下拉框（选择企业官网）
            cur_site_id = c.get("site_id", "")
            mapped_site = cols[2].selectbox(
                f"映射站点",
                [""] + list(site_opts_for_comp.keys()),
                format_func=lambda x: "▼ 无" if x == "" else site_opts_for_comp.get(x, x),
                index=0 if not cur_site_id else (list(site_opts_for_comp.keys()).index(cur_site_id) + 1) if cur_site_id in site_opts_for_comp else 0,
                key=f"comp_site_{i}",
                help="关联的企业官网站点：company 类型站点将自动归属此公司",
            )
            ckw = cols[3].text_input(f"关键词（逗号分隔）", value=",".join(c.get("keywords", [])), key=f"comp_kw_{i}")
            if cols[4].button("🗑 删除", key=f"comp_del_{i}"):
                companies.pop(i)
                edited = True
                st.rerun()
            # 实时更新
            companies[i] = {
                "id": cid,
                "name": cname,
                "keywords": [k.strip() for k in ckw.split(",") if k.strip()],
                "site_id": mapped_site if mapped_site else (c.get("site_id", "")),
            }

    # 新增公司行
    with st.expander("➕ 新增公司"):
        cols_new = st.columns(3)
        new_id = cols_new[0].text_input("公司 ID", key="new_comp_id", placeholder="e.g. verticalmag")
        new_name = cols_new[1].text_input("公司名称", key="new_comp_name", placeholder="e.g. Vertical Magazine")
        new_site = cols_new[2].selectbox(
            "映射站点（可选）",
            [""] + list(site_opts_for_comp.keys()),
            format_func=lambda x: "▼ 无" if x == "" else site_opts_for_comp.get(x, x),
            key="new_comp_site",
            help="关联的企业官网：company 类型站点将自动归属此公司",
        )
        new_kw = st.text_input("关键词（逗号分隔）", key="new_comp_kw", placeholder="e.g. vertical,verticalmag")
        if st.button("添加", type="primary"):
            if new_id and new_name:
                entry = {
                    "id": new_id,
                    "name": new_name,
                    "keywords": [k.strip() for k in new_kw.split(",") if k.strip()],
                }
                if new_site:
                    entry["site_id"] = new_site
                companies.append(entry)
                db.save_company_list(companies)
                st.success(f"已添加 {new_name}")
                st.rerun()

    if st.button("保存公司列表", type="primary"):
        db.save_company_list(companies)
        st.success("公司列表已保存！")
        st.rerun()

    st.divider()

    st.subheader("快速注入预设站点")
    if st.button("注入 GitHub Blog 预设"):
        from cli import DEMO_SITES
        for site in DEMO_SITES:
            db.save_site(site)
        st.success("已注入预设站点！")
        st.rerun()
