"""把简报数据渲染成一个可以离线打开的 HTML 页面（手机、邮件都适用）。"""
from __future__ import annotations

import html
from datetime import date
from pathlib import Path

from .config import ROOT

WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]


def _esc(s: str) -> str:
    return html.escape(str(s or ""), quote=True)


def _fmt_date(d: date) -> str:
    return f"{d.year}年{d.month}月{d.day}日 星期{WEEKDAYS[d.weekday()]}"


def _badge(item: dict) -> str:
    conf = item.get("confidence", "")
    n = item.get("n_sources", 0)
    if conf == "多源确认":
        return f'<span class="badge ok">多源确认 · {n} 家</span>'
    if conf == "部分待核实":
        return '<span class="badge warn">部分待核实</span>'
    if conf == "单一来源":
        return '<span class="badge single">单一来源</span>'
    return ""


def _sources(item: dict) -> str:
    parts = []
    for s in item.get("sources", [])[:4]:
        parts.append(f'<a href="{_esc(s["url"])}" target="_blank" rel="noopener">{_esc(s["name"])}</a>')
    return " · ".join(parts)


def _css() -> str:
    css_file = ROOT / "web" / "style.css"
    try:
        return css_file.read_text(encoding="utf-8")
    except OSError:
        return ""


PROGRESS_BAR = """
  <div id="prog" class="prog" hidden>
    <div class="prog-row">
      <span id="prog-msg">正在准备……</span>
      <span class="prog-right"><span id="prog-el"></span><span id="prog-pct">0%</span></span>
    </div>
    <div class="prog-track"><div id="prog-bar" class="prog-bar"></div></div>
  </div>"""

STATUS_JS = """
<script>
(function () {
  var bar = document.getElementById('prog'),
      msg = document.getElementById('prog-msg'),
      pct = document.getElementById('prog-pct'),
      el  = document.getElementById('prog-el'),
      fill= document.getElementById('prog-bar'),
      btn = document.getElementById('regen'),
      rmsg= document.getElementById('regen-msg'),
      armed = false;

  function show() { if (bar) bar.hidden = false; }

  function render(s) {
    if (s.state === 'running') {
      armed = true; show();
      msg.textContent = s.message || '处理中……';
      pct.textContent = (s.percent || 0) + '%';
      fill.style.width = (s.percent || 0) + '%';
      el.textContent = s.elapsed ? ('已用 ' + s.elapsed + ' 秒') : '';
      if (btn) { btn.disabled = true; btn.textContent = '生成中……'; }
    } else if (armed) {
      if (s.state === 'done') {
        msg.textContent = '完成，正在刷新……';
        fill.style.width = '100%';
        pct.textContent = '100%';
        setTimeout(function () { location.reload(); }, 600);
        return;
      }
      if (s.state === 'error') {
        msg.textContent = '出错：' + (s.message || '未知错误');
        pct.textContent = ''; fill.style.width = '0';
        if (btn) { btn.disabled = false; btn.textContent = '重新生成'; }
        armed = false;
      }
    }
  }

  async function poll() {
    try {
      var r = await fetch('/api/status', { cache: 'no-store' });
      if (r.ok) render(await r.json());
    } catch (e) {}
  }

  async function start() {
    if (btn && !confirm('重新生成会再调用一次 AI（约 1 分钟、几分钱），确定吗？')) return;
    if (btn) { btn.disabled = true; btn.textContent = '生成中……'; }
    if (rmsg) rmsg.textContent = '';
    try {
      var r = await fetch('/api/generate', { method: 'POST' });
      if (!r.ok) { if (rmsg) rmsg.textContent = '已经在生成了'; return; }
      armed = true; show(); poll();
    } catch (e) {
      if (rmsg) rmsg.textContent = '连不上本地服务，请确认那个黑窗口还在运行';
    }
  }

  if (btn) btn.addEventListener('click', start);
  poll();
  setInterval(poll, 1200);
})();
</script>"""


def render_index(dates: list[str]) -> str:
    """云端存档首页：按日期列出所有已生成的简报。"""
    rows = []
    for d in dates:
        try:
            label = _fmt_date(date.fromisoformat(d))
        except ValueError:
            label = d
        rows.append(f'<li><a href="{_esc(d)}.html"><span class="idx-date">{_esc(label)}</span></a></li>')
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>每日国际简报 · 存档</title><style>{_css()}</style></head>
<body><div class="wrap">
<header class="masthead">
<div class="kicker">DAILY WORLD BRIEF</div><h1>每日国际简报</h1>
<div class="dateline">存档 · 共 {len(dates)} 期</div></header>
<ul class="archive">{''.join(rows)}</ul>
</div></body></html>"""


def render_report(digest: dict, meta: dict) -> str:
    d = date.fromisoformat(digest.get("date") or date.today().isoformat())
    title = digest.get("title") or f"{d.isoformat()} 国际简报"
    fallback = digest.get("fallback")
    ai_note = "" if not fallback else '<div class="notice">当前是未接入 AI 的降级版：只有标题和来源，没有翻译与解读。</div>'

    top3_html = ""
    for i, t in enumerate(digest.get("top3", []), 1):
        top3_html += f"""
      <li>
        <span class="num">{i}</span>
        <div><h3>{_esc(t.get('title'))}</h3><p>{_esc(t.get('summary'))}</p></div>
      </li>"""

    sections_html = ""
    for sec in digest.get("sections", []):
        items_html = ""
        for it in sec.get("items", []):
            why = f'<p class="why"><span class="label">为何重要</span>{_esc(it.get("why"))}</p>' if it.get("why") else ""
            note = f'<p class="note">{_esc(it.get("note"))}</p>' if it.get("note") else ""
            items_html += f"""
        <article class="item">
          <div class="item-top"><h3>{_esc(it.get('title'))}</h3>{_badge(it)}</div>
          <p class="fact">{_esc(it.get('fact'))}</p>
          {why}{note}
          <div class="meta"><span>{_esc(it.get('time'))}</span><span class="dot">·</span><span class="src">{_sources(it)}</span></div>
        </article>"""
        sections_html += f"""
    <section class="block">
      <h2>{_esc(sec.get('name'))}</h2>{items_html}
    </section>"""

    caveats = digest.get("caveats") or []
    caveats_html = ""
    if caveats:
        lis = "".join(f"<li>{_esc(c)}</li>" for c in caveats)
        caveats_html = f'<div class="caveats"><h4>说明</h4><ul>{lis}</ul></div>'

    updated = _esc(meta.get("updated", ""))
    stats = meta.get("stats", {})
    stat_line = (
        f'{stats.get("articles", 0)} 条原始报道 · {stats.get("events", 0)} 个事件 · '
        f'来源 {stats.get("sources_ok", 0)}/{stats.get("sources_total", 0)} 家正常'
    )
    if stats.get("tokens"):
        stat_line += f' · AI 用量 {stats["tokens"] / 1000:.1f}k tokens'
    history = meta.get("history") or []
    nav = ""
    if history:
        links = " ".join(
            f'<a class="{"on" if h == d.isoformat() else ""}" href="/r/{h}.html">{h[5:]}</a>' for h in history[:10]
        )
        nav = f'<nav class="history"><span>近十日</span>{links}</nav>'

    model_line = "DeepSeek 撰写并核查" if not fallback else "未接入 AI"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>{_css()}</style>
</head>
<body>
<div class="wrap">{PROGRESS_BAR}
  <header class="masthead">
    <div class="kicker">DAILY WORLD BRIEF</div>
    <h1>每日国际简报</h1>
    <div class="dateline"><span>{_fmt_date(d)}</span><span class="sep">·</span><span>{updated} 更新</span></div>
  </header>

  {ai_note}
  <p class="lede">{_esc(digest.get('one_line'))}</p>

  <section class="top3">
    <div class="sec-label">今日三件事</div>
    <ol>{top3_html}
    </ol>
  </section>

  {sections_html}
  {caveats_html}

  <footer>
    {nav}
    <div class="actions">
      <button id="regen" class="btn small" type="button">重新生成</button>
      <span id="regen-msg" class="msg"></span>
    </div>
    <p class="colophon">{_esc(stat_line)}</p>
    <p class="colophon">{_esc(model_line)} · 每条均附原始报道链接，可信度按“是否有 ≥2 家独立媒体确认”标注</p>
  </footer>
</div>
{STATUS_JS}
</body>
</html>"""


def render_empty() -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>每日国际简报</title><style>{_css()}</style></head>
<body><div class="wrap">{PROGRESS_BAR}
<header class="masthead">
<div class="kicker">DAILY WORLD BRIEF</div><h1>每日国际简报</h1></header>
<div class="notice empty">
<h3>还没有生成简报</h3>
<p>点下面的按钮生成今天的简报：抓取十几家媒体、归并同一事件、AI 撰写并核查，大约 1 分钟。</p>
<div class="actions">
  <button id="regen" class="btn" type="button">生成今天的简报</button>
  <span id="regen-msg" class="msg"></span>
</div>
</div></div>
{STATUS_JS}
</body></html>"""
