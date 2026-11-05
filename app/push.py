"""把简报推到手机：邮件（QQ/163 邮箱）或 Server酱 之类的 webhook。"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr

from .summarize import SECTION_NAMES


def plain_text(digest: dict) -> str:
    lines = [f"〔{digest.get('title')}〕", "", digest.get("one_line", ""), "", "—— 今日三件事 ——"]
    for i, t in enumerate(digest.get("top3", []), 1):
        lines.append(f"{i}. {t.get('title')}  {t.get('summary')}")
    for sec in digest.get("sections", []):
        lines.append("")
        lines.append(f"—— {sec['name']} ——")
        for it in sec["items"]:
            lines.append(f"· {it['title']}（{it.get('confidence')}）")
            lines.append(f"  {it['fact']}")
            if it.get("why"):
                lines.append(f"  为何重要：{it['why']}")
            srcs = "、".join(s["name"] for s in it.get("sources", [])[:3])
            lines.append(f"  来源：{srcs}")
    for c in digest.get("caveats", []):
        lines.append("")
        lines.append("说明：" + c)
    return "\n".join(lines)


def send_email(digest: dict, html: str, secrets: dict) -> str:
    cfg = secrets.get("email") or {}
    if not cfg.get("enabled"):
        return "邮件推送未开启"
    host = cfg.get("smtp_host", "smtp.qq.com")
    port = int(cfg.get("smtp_port", 465))
    sender = cfg.get("sender", "")
    auth = cfg.get("auth_code", "")
    recipients = cfg.get("recipients") or []
    if not (sender and auth and recipients):
        return "邮件配置不完整（sender / auth_code / recipients）"

    msg = EmailMessage()
    msg["Subject"] = f"{digest.get('title')}｜{digest.get('one_line', '')[:40]}"
    msg["From"] = formataddr(("每日国际简报", sender))
    msg["To"] = ", ".join(recipients)
    msg.set_content(plain_text(digest))
    msg.add_alternative(html, subtype="html")

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
        s.login(sender, auth)
        s.send_message(msg)
    return f"邮件已发送给 {len(recipients)} 个地址"


def brief_markdown(digest: dict) -> str:
    """Server酱 / 微信里显示用的 Markdown。"""
    lines = [f"## {digest.get('title', '')}", "", f"> {digest.get('one_line', '')}", "", "**今日三件事**", ""]
    for i, t in enumerate(digest.get("top3", []), 1):
        lines.append(f"{i}. **{t.get('title')}** — {t.get('summary')}")
    for sec in digest.get("sections", []):
        lines.append("")
        lines.append(f"### {sec['name']}")
        for it in sec["items"]:
            lines.append("")
            lines.append(f"**{it['title']}**  `{it.get('confidence', '')}`")
            lines.append(it.get("fact", ""))
            if it.get("why"):
                lines.append(f"> 为何重要：{it['why']}")
            src = it.get("sources", [])
            if src:
                lines.append("来源：" + "、".join(f"[{s['name']}]({s['url']})" for s in src[:3]))
    for c in digest.get("caveats", []):
        lines.append("")
        lines.append(f"*{c}*")
    page = os.environ.get("BRIEF_PAGE_URL")
    if page:
        lines.append("")
        lines.append(f"[在网页上查看排版版]({page})")
    return "\n".join(lines)


def send_webhook(digest: dict, secrets: dict) -> str:
    cfg = secrets.get("webhook") or {}
    if not cfg.get("enabled"):
        return "微信推送未开启"
    url = (cfg.get("url") or "").strip()
    sendkey = (cfg.get("sendkey") or "").strip()
    if not url and sendkey:
        url = f"https://sctapi.ftqq.com/{sendkey}.send"
    if not url:
        return "微信推送地址为空（填 sendkey 或 url）"

    is_sct = "sctapi.ftqq.com" in url
    payload = {
        "title": digest.get("title", "每日国际简报"),
        "desp": brief_markdown(digest)[:20000],
    }
    if not is_sct:
        payload.update({"content": payload["desp"], "short": digest.get("one_line", "")})

    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read().decode("utf-8", "replace")
    try:
        data = json.loads(body)
        if is_sct and data.get("code") != 0:
            return f"微信推送失败：{data.get('message') or data}"
    except json.JSONDecodeError:
        pass
    return "微信推送已发送"


def push_all(digest: dict, html: str, secrets: dict) -> list[str]:
    results = []
    for fn in (send_email, lambda d, h, s: send_webhook(d, s)):
        try:
            results.append(fn(digest, html, secrets))
        except Exception as e:  # noqa: BLE001
            results.append(f"推送失败：{type(e).__name__}: {e}")
    return results
