"""LLM 智能处理层 — OpenAI / Ollama 双模式"""
import json
import logging
import re
from typing import Optional

from openai import AsyncOpenAI

from models import RawPage, IntelItem
from core.database import DatabaseHandler

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TPL = """你是一个情报监控 AI 助理。你的任务是提取以下网页内容的情报字段，输出 JSON。

输出字段：
- event_time: 事件发生时间，格式 YYYY-MM-DD；无法确定则为空字符串。
- clean_title: 清理后的新闻标题。去除日期前缀、分类标签等多余字符，只保留纯粹标题。保持原文语言。
- summary: 「必须用简体中文」一句话精炼摘要（≤60字），客观陈述事实，不加评论。
- category: 最佳匹配分类（从预设列表中选择）。
- company: 该新闻归属于哪家公司。从公司列表中选择最匹配的一家，如果不归属任何所列公司则为空字符串。
- relevant: 布尔值，判断内容是否与 "{topic}" 相关。

预设分类列表：{categories}
公司列表（company 字段从中选一）：{companies}"""


class LLMProcessor:
    """使用 LLM 进行内容清洗与结构化"""

    def __init__(
        self,
        mode: str = "openai",
        api_key: str = "",
        base_url: str = "",
        model: str = "gpt-4o-mini",
        ollama_base_url: str = "http://localhost:11434/v1",
    ):
        self.mode = mode
        self._api_key = api_key
        self.model = model
        if mode == "ollama":
            self.client = AsyncOpenAI(
                api_key="ollama",
                base_url=ollama_base_url,
            )
        else:
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url or "https://api.openai.com/v1",
            )

    @property
    def is_configured(self) -> bool:
        """LLM 是否已配置 API Key"""
        return bool(self._api_key)

    async def evaluate_extraction_quality(self, url: str, items: list[dict]) -> dict:
        """
        评估一批提取到的候选条目质量，返回过滤建议。

        items: [{title, link, time_snippet?}, ...]
        返回: {
            "good_indices": [...],        # 有效新闻条目的索引
            "junk_indices": [...],         # 垃圾条目的索引
            "quality_score": 0.8,          # 0-1 质量评分
            "reasoning": "...",            # 简要说明
        }
        """
        lines = []
        for i, item in enumerate(items):
            t = item.get("title", "")[:80]
            l = item.get("link", "")[:60]
            lines.append(f"[{i}] 标题: {t}  |  链接: {l}")
        items_text = "\n".join(lines)

        prompt = f"""你是一个网页爬取质量评估专家。下面是从一个页面提取到的候选条目列表，
请你判断哪些是真正的新闻/文章标题，哪些是导航链接、页脚、阅读全文、广告等垃圾内容。

页面URL: {url}

候选条目:
{items_text}

判断标准：
- 有效新闻：标题 > 6 字符，有实际信息量，不含「阅读全文」「>>」等垃圾文本
- 垃圾内容：导航文字（登录、注册、首页）、阅读全文、页脚链接、标题过短、重复的无意义文本

返回 JSON（只返回 JSON，不要多余文字）：
{{
  "good_indices": [有效新闻的索引数组],
  "junk_indices": [垃圾条目的索引数组],
  "quality_score": 0.0 到 1.0 之间的浮点数（有效占比越高分越高）,
  "reasoning": "简要说明判断依据"
}}"""
        try:
            kwargs = dict(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是一个网页内容质量评估助手。只输出JSON，不要多余文字。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
            )
            if self.mode != "ollama":
                kwargs["response_format"] = {"type": "json_object"}
            resp = await self.client.chat.completions.create(**kwargs)
            text = resp.choices[0].message.content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                text = text.rsplit("\n", 1)[0]
                if text.endswith("```"):
                    text = text[:-3]
            result = json.loads(text)
            return result
        except Exception as e:
            logger.warning(f"LLM 评估提取质量失败: {e}")
            return {"good_indices": list(range(len(items))), "junk_indices": [], "quality_score": 0.5, "reasoning": f"评估异常: {e}"}




    def _match_company_by_keyword(self, title: str, companies: list[dict]) -> str:
        """标题关键词匹配公司（0 token），返回逗号分隔的公司ID列表"""
        title_lower = title.lower()
        matched = []
        for c in companies:
            for kw in c.get("keywords", []):
                if kw.lower() in title_lower:
                    matched.append(c["id"])
                    break  # 每家公司最多匹配一次
        return ",".join(matched)

    async def process(
        self,
        page: RawPage,
        topic: str,
        categories: list[str],
        source_type: str = "company",
        companies: list[dict] = None,
    ) -> Optional[IntelItem]:
        """清洗一条原始页面内容，返回结构化情报（或 None 表示跳过）"""
        companies = companies or []

        # ── 企业官网 → 直接确定公司归属，不走LLM过滤 ──
        if source_type == "company":
            company_id = ""
            for c in companies:
                if c.get("site_id") == page.site_id:
                    company_id = c["id"]
                    break
            if not company_id:
                company_id = ""

            item = await self._call_llm(page, topic, categories, companies)
            if item is None:
                return None
            item.company = company_id
            return item

        # ── 垂类媒体 → 关键词匹配公司 + LLM 判断相关/分类/公司 ──
        # 先标题关键词匹配
        company_kw = self._match_company_by_keyword(page.title, companies)
        item = await self._call_llm(page, topic, categories, companies, check_relevant=True)
        if item is None:
            return None

        # 公司归属：关键词匹配优先，LLM 结果补缺
        if company_kw:
            item.company = company_kw
        else:
            llm_company = getattr(item, "company", "") or ""
            if llm_company and any(c["id"] == llm_company for c in companies):
                item.company = llm_company

        return item

    async def _call_llm(
        self,
        page: RawPage,
        topic: str,
        categories: list[str],
        companies: list[dict],
        check_relevant: bool = False,
    ) -> Optional[IntelItem]:
        """实际调用 LLM（含自动重试）"""
        company_names = [c["name"] for c in companies]
        company_names_str = json.dumps(company_names, ensure_ascii=False)

        user_content = (
            f"## 标题\n{page.title}\n\n"
            f"## 正文内容\n{page.raw_text[:3000]}\n\n"
            f"## 来源URL\n{page.source_url}"
        )

        last_exc = None
        for attempt in range(3):
            try:
                resp = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": SYSTEM_PROMPT_TPL.format(
                                topic=topic,
                                categories=json.dumps(categories, ensure_ascii=False),
                                companies=company_names_str,
                            ),
                        },
                        {"role": "user", "content": user_content},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    max_tokens=350,
                )
                last_exc = None
                break
            except Exception as e:
                last_exc = e
                logger.warning(f"LLM 调用失败(重试 {attempt+1}/3): {e}")
                if attempt < 2:
                    import asyncio
                    await asyncio.sleep(2 ** attempt)  # 指数退避: 1s, 2s
                continue

        if last_exc:
            logger.error(f"LLM 调用最终失败(已重试3次): {last_exc}")
            return None

        try:
            data = json.loads(resp.choices[0].message.content)
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.warning(f"LLM 返回非 JSON: {e}")
            return None

        # 垂类媒体需要判断相关性
        if check_relevant and not data.get("relevant", True):
            logger.info(f"LLM 判定不相关，跳过: {page.source_url}")
            return None

        fingerprint = DatabaseHandler.make_fingerprint(
            page.source_url, page.raw_text)

        # 时间提取：LLM → HTML时间 → 标题
        from models import normalize_time
        event_time = data.get("event_time", "") or ""
        if not event_time:
            if page.time_snippet:
                event_time = normalize_time(page.time_snippet)
            if not event_time and page.title:
                event_time = normalize_time(page.title)

        clean_title = data.get("clean_title", "").strip()
        final_title = clean_title if clean_title else page.title

        summary = data.get("summary")
        if not summary or not summary.strip():
            summary = clean_title if clean_title else page.title
        else:
            summary = summary.strip()

        llm_company = data.get("company", "") or ""

        return IntelItem(
            fingerprint=fingerprint,
            event_time=event_time,
            summary=summary,
            source_url=page.source_url,
            category=data.get("category", "其他"),
            site_id=page.site_id,
            raw_title=page.title,
            clean_title=clean_title,
            company=llm_company,
            crawled_at=page.crawled_at,
        )
