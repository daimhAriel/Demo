"""
推送通知引擎
支持：飞书群机器人、邮件（SMTP）
"""
import json
import logging
from datetime import datetime

import requests

from models import IntelItem

logger = logging.getLogger(__name__)

NOTIF_CONFIG_KEY = "notifier_config"


_DEFAULT_CONFIG = {
    "enabled": False,
    "channels": {
        "feishu": {
            "enabled": True,
            "webhook_url": "",
        },
        "email": {
            "enabled": False,
            "smtp_server": "",
            "smtp_port": 587,
            "smtp_user": "",
            "smtp_password": "",
            "from_addr": "",
            "to_addrs": "",
            "use_tls": True,
        },
    },
}


class Notifier:
    """推送通知引擎"""

    def __init__(self, db):
        self.db = db
        self.config = self._load_config()

    # ── 配置管理 ──

    def _load_config(self) -> dict:
        raw = self.db.get_app_config(NOTIF_CONFIG_KEY, "")
        if not raw:
            return dict(_DEFAULT_CONFIG)
        try:
            cfg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return dict(_DEFAULT_CONFIG)

        # 以默认配置为基础，只叠加已保存配置中匹配的通道
        result = dict(_DEFAULT_CONFIG)
        result["enabled"] = cfg.get("enabled", False)
        for ch in _DEFAULT_CONFIG["channels"]:
            saved = cfg.get("channels", {}).get(ch)
            if saved and isinstance(saved, dict):
                result["channels"][ch].update(saved)
        return result

    def save_config(self, config: dict):
        self.config = config
        self.db.set_app_config(NOTIF_CONFIG_KEY, json.dumps(config, ensure_ascii=False))

    def get_config(self) -> dict:
        return self.config

    def set_enabled(self, enabled: bool):
        self.config["enabled"] = enabled
        self.save_config(self.config)

    # ── 通知发送 ──

    def send(self, item: IntelItem, site_name: str = "") -> dict:
        """
        发送通知，返回各通道结果 {channel_name: "ok"/错误信息}
        """
        if not self.config.get("enabled", False):
            return {"status": "disabled"}

        results = {}
        for channel, cfg in self.config.get("channels", {}).items():
            if not cfg.get("enabled", True):
                continue
            try:
                if channel == "feishu":
                    results[channel] = self._send_feishu(item, cfg, site_name)
                elif channel == "email":
                    results[channel] = self._send_email(item, cfg, site_name)
                else:
                    results[channel] = f"unknown channel: {channel}"
            except Exception as e:
                logger.error(f"通知发送失败 [{channel}]: {e}")
                results[channel] = f"error: {e}"

        # 标记已推送
        if any(v == "ok" for v in results.values()):
            try:
                self.db.update_item_fields(
                    item.fingerprint,
                    pushed=1,
                    pushed_at=datetime.now().isoformat(),
                )
            except Exception as e:
                logger.warning(f"标记推送状态失败: {e}")

        return results

    def send_test(self) -> dict:
        """发送测试通知"""
        dummy = IntelItem(
            fingerprint="test_00000000",
            summary="这是一条测试通知 🎉 如果看到此消息，说明通知配置正确！",
            event_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            category="测试",
            source_url="https://github.com",
            site_id="test",
            raw_title="测试通知",
            company="",
            crawled_at=datetime.now().isoformat(),
        )
        return self.send(dummy, site_name="测试站点")

    # ── 各通道实现 ──

    def _build_markdown_content(self, item: IntelItem, site_name: str) -> str:
        """构建飞书 lark_md 格式的文本"""
        title = item.clean_title or item.raw_title or item.summary[:60]
        lines = [
            f"**标题:** {title}",
            f"**摘要:** {item.summary}",
            f"**分类:** {item.category}",
            f"**时间:** {item.event_time or '未知'}",
        ]
        if item.company:
            lines.append(f"**公司:** {item.company}")
        if site_name:
            lines.append(f"**来源:** {site_name}")
        return "\n".join(lines)

    def _send_feishu(self, item: IntelItem, cfg: dict, site_name: str) -> str:
        """飞书群机器人 Webhook（消息卡片格式）"""
        url = cfg.get("webhook_url", "").strip()
        if not url:
            return "webhook_url 为空"

        title_short = (item.clean_title or item.raw_title or item.summary)[:40]
        content = self._build_markdown_content(item, site_name)

        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"🛩️ {title_short}",
                    },
                    "template": "blue",
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": content,
                        },
                    },
                    {"tag": "hr"},
                    {
                        "tag": "action",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "查看原文"},
                                "url": item.source_url,
                                "type": "default",
                            }
                        ],
                    },
                ],
            },
        }

        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            return f"飞书返回错误: {body.get('msg', body)}"
        return "ok"

    def _send_email(self, item: IntelItem, cfg: dict, site_name: str) -> str:
        """Email via SMTP"""
        import smtplib
        from email.message import EmailMessage

        smtp_server = cfg.get("smtp_server", "").strip()
        smtp_port = int(cfg.get("smtp_port", 587))
        smtp_user = cfg.get("smtp_user", "").strip()
        smtp_password = cfg.get("smtp_password", "").strip()
        from_addr = cfg.get("from_addr", "").strip()
        to_addrs = cfg.get("to_addrs", "").strip()

        if not all([smtp_server, smtp_user, smtp_password, from_addr, to_addrs]):
            return "SMTP 配置不完整"

        title = item.clean_title or item.raw_title or item.summary[:60]
        text = self._build_markdown_content(item, site_name)
        # 转纯文本格式
        text_plain = text.replace("**", "")

        msg = EmailMessage()
        msg["Subject"] = f"[情报监控] {title}"
        msg["From"] = from_addr
        msg["To"] = to_addrs
        msg.set_content(text_plain)

        use_tls = cfg.get("use_tls", True)
        if use_tls:
            with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as server:
                server.starttls()
                server.login(smtp_user, smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=15) as server:
                server.login(smtp_user, smtp_password)
                server.send_message(msg)

        return "ok"
