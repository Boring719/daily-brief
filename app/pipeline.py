"""主流程：抓取 → 聚类 → 挑选 → AI 撰写 → 核查 → 渲染 → 存档 → 推送。"""
from __future__ import annotations

import json
import re
import time
import traceback
from datetime import date, datetime

from .cluster import cluster_articles, event_score
from .config import REPORT_DIR, ensure_dirs, has_api_key, load_secrets, load_settings
from .fetch import fetch_all
from .render import render_index, render_report
from .store import list_dates, log_run, save_report
from .summarize import LLMError, USAGE, curate, digest, fallback_digest, reset_usage, verify

Progress = "callable[[str, int], None]"


def run(
    date_str: str | None = None,
    push: bool = False,
    force: bool = False,
    progress: Progress | None = None,
) -> dict:
    def say(msg: str, pct: int = 0) -> None:
        if progress:
            progress(msg, pct)

    ensure_dirs()
    settings = load_settings()
    secrets = load_secrets()
    d = date_str or date.today().isoformat()
    started = datetime.now()
    say("正在抓取各家新闻源……", 6)

    try:
        articles, health = fetch_all(
            settings["sources"],
            hours=int(settings.get("window_hours", 26)),
            limit=int(settings.get("max_articles_per_source", 40)),
            on_progress=lambda done, total: say(
                f"正在抓取新闻源（{done}/{total} 家已返回）……", 6 + int(18 * done / max(total, 1))
            ),
            deadline_seconds=int(settings.get("fetch_deadline_seconds", 25)),
        )
        if len(articles) < 5:
            raise RuntimeError("几乎没有抓到新闻，请检查网络连接后重试")

        say(f"抓到 {len(articles)} 条报道，正在归并同一事件……", 24)
        events = cluster_articles(articles)
        now_ts = time.time()
        events.sort(key=lambda e: -event_score(e, now_ts))

        ai_ready = has_api_key(secrets)
        count = int(settings.get("digest_item_count", 14))
        reset_usage()

        if ai_ready:
            pool = _prep_pool(articles, cap=240)
            say(f"共 {len(events)} 个事件，正在让 AI 筛选当天要闻……", 36)
            picked = curate(pool, settings, secrets["deepseek_api_key"], count)
            if not picked:
                raise LLMError("AI 没有挑出任何事件，稍后重试")
            say(f"选定 {len(picked)} 条，正在撰写中文简报……", 52)
            digest_data = digest(picked, settings, secrets["deepseek_api_key"], d)
            say("正在做第二道事实核查……", 80)
            digest_data = verify(digest_data, picked, settings, secrets["deepseek_api_key"])
        else:
            picked = [
                {"event": ev, "section": _section_of(ev.primary.region), "importance": 3}
                for ev in events[:count]
            ]
            digest_data = fallback_digest(picked, d)

        stats = {
            "articles": len(articles),
            "events": len(events),
            "sources_ok": sum(1 for h in health if h["ok"]),
            "sources_total": len(health),
            "picked": len(picked),
            "seconds": int((datetime.now() - started).total_seconds()),
            "tokens": USAGE["prompt_tokens"] + USAGE["completion_tokens"],
            "calls": USAGE["calls"],
        }
        say("正在排版……", 93)
        meta = {
            "updated": datetime.now().strftime("%H:%M"),
            "stats": stats,
            "history": list_dates()[:10],
        }
        html = render_report(digest_data, meta)

        (REPORT_DIR / f"{d}.html").write_text(html, encoding="utf-8")
        save_report(d, digest_data, html, {"stats": stats, "health": health})
        _write_index()

        push_results: list[str] = []
        if push:
            from .push import push_all

            say("正在推送到手机……", 97)
            push_results = push_all(digest_data, html, secrets)

        message = f"完成：{stats['picked']} 条要闻，用时 {stats['seconds']} 秒"
        if push_results:
            message += "｜" + "；".join(push_results)
        log_run(d, True, message, started)
        say(message, 100)
        return {"ok": True, "date": d, "message": message, "stats": stats, "push": push_results}
    except Exception as e:  # noqa: BLE001
        message = f"{type(e).__name__}: {e}"
        log_run(d, False, message + "\n" + traceback.format_exc()[:1500], started)
        say("出错：" + message, 0)
        return {"ok": False, "date": d, "message": message}


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _write_index() -> None:
    dates = sorted({p.stem for p in REPORT_DIR.glob("*.html") if DATE_RE.match(p.stem)}, reverse=True)
    (REPORT_DIR / "index.html").write_text(render_index(dates), encoding="utf-8")


def _section_of(region: str) -> str:
    return {
        "us": "美欧",
        "europe": "美欧",
        "mideast": "中东",
        "asia": "亚太",
        "tech": "经济·科技",
        "africa": "非洲·拉美",
        "latam": "非洲·拉美",
    }.get(region, "美欧")


JUNK = re.compile(
    r"(podcast|opinion|editorial|horoscope|crossword|recipe|quiz|what to watch|"
    r"演唱会|春晚|综艺|选秀|球星|转会|中超|彩票|星座|票房|绯闻|网红|探店|菜谱|旅游攻略)",
    re.IGNORECASE,
)


def _prep_pool(articles: list, cap: int = 240) -> list:
    """去掉明显的软文/体育娱乐，按来源轮流取，避免某一家刷屏。"""
    seen: set[str] = set()
    by_source: dict[str, list] = {}
    for a in articles:
        if JUNK.search(a.title):
            continue
        key = re.sub(r"[\W_]+", "", a.title.lower())[:60]
        if key in seen:
            continue
        seen.add(key)
        by_source.setdefault(a.source_id, []).append(a)
    for lst in by_source.values():
        lst.sort(key=lambda a: a.published, reverse=True)

    pool, idx = [], 0
    while len(pool) < cap and any(idx < len(v) for v in by_source.values()):
        for lst in by_source.values():
            if idx < len(lst):
                pool.append(lst[idx])
                if len(pool) >= cap:
                    break
        idx += 1
    return pool


def rerender(date_str: str | None = None) -> dict:
    """不重新抓取、不花钱，只按当前样式重排已有简报（改 CSS 后用它）。"""
    from .store import get_report

    d = date_str or date.today().isoformat()
    row = get_report(d)
    if not row:
        return {"ok": False, "message": f"没有 {d} 的简报"}
    digest_data = json.loads(row["digest_json"])
    stats = json.loads(row["stats_json"]).get("stats", {})
    meta = {
        "updated": (row["generated_at"] or "")[11:16],
        "stats": stats,
        "history": list_dates()[:10],
    }
    html = render_report(digest_data, meta)
    (REPORT_DIR / f"{d}.html").write_text(html, encoding="utf-8")
    save_report(d, digest_data, html, {"stats": stats})
    return {"ok": True, "message": f"{d} 已重排"}


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="生成每日国际简报")
    p.add_argument("--date", default=None)
    p.add_argument("--push", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--render-only", action="store_true", help="只重排已有简报，不重新抓取")
    args = p.parse_args()
    if args.render_only:
        print(json.dumps(rerender(args.date), ensure_ascii=False, indent=2))
    else:
        res = run(args.date, push=args.push, force=args.force, progress=lambda m, p=0: print(m, flush=True))
        print(json.dumps(res, ensure_ascii=False, indent=2))
