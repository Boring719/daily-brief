"""抓取各家新闻源的 RSS，统一成 Article。"""
from __future__ import annotations

import gzip
import re
import socket
import time
import urllib.error
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from lxml import etree

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_HTML_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


@dataclass
class Article:
    source_id: str
    source_name: str
    region: str
    lang: str
    tier: str
    title: str
    link: str
    published: datetime
    summary: str = ""

    def snippet(self, n: int = 320) -> str:
        return _WS.sub(" ", self.summary).strip()[:n]


def _http_get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
            "Accept-Encoding": "gzip, identity",
            "Accept-Language": "en-US,en;q=0.8,zh-CN;q=0.6",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        if resp.headers.get("Content-Encoding", "").lower() == "gzip" or data[:2] == b"\x1f\x8b":
            try:
                data = gzip.decompress(data)
            except OSError:
                pass
        return data


def _tag(el) -> str:
    t = el.tag
    if isinstance(t, str):
        return t.split("}")[-1].lower()
    return ""


def _find(el, *names: str):
    for child in el.iter():
        if _tag(child) in names:
            return child
    return None


def _text(el) -> str:
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def _clean_html(text: str) -> str:
    text = _HTML_TAG.sub(" ", text or "")
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&apos;", "'")
    )
    return _WS.sub(" ", text).strip()


def _parse_date(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _item_link(el) -> str:
    link = _find(el, "link")
    if link is None:
        return ""
    href = link.get("href")
    if href:
        return href.strip()
    return _text(link)


def parse_feed(data: bytes, source: dict, since: datetime) -> list[Article]:
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)
    root = etree.fromstring(data, parser)
    if root is None:
        raise ValueError("无法解析 XML")

    items = [el for el in root.iter() if _tag(el) in ("item", "entry")]
    out: list[Article] = []
    for el in items:
        title = _clean_html(_text(_find(el, "title")))
        link = _item_link(el)
        if not title or not link.startswith("http"):
            continue
        raw_date = _text(_find(el, "pubdate", "published", "updated", "date")) or ""
        published = _parse_date(raw_date) or datetime.now(timezone.utc)
        # 有些媒体会给出未来时间，夹到当前，避免排序错乱
        now = datetime.now(timezone.utc)
        if published > now + timedelta(minutes=10):
            published = now
        if published < since:
            continue
        desc = _text(_find(el, "description", "summary", "content", "encoded"))
        out.append(
            Article(
                source_id=source["id"],
                source_name=source["name"],
                region=source.get("region", "global"),
                lang=source.get("lang", "en"),
                tier=source.get("tier", "B"),
                title=title,
                link=link,
                published=published,
                summary=_clean_html(desc)[:800],
            )
        )
    # 同一来源内按标题去重，保留最新
    seen: dict[str, Article] = {}
    for a in out:
        key = a.title.lower()[:80]
        if key not in seen or a.published > seen[key].published:
            seen[key] = a
    return sorted(seen.values(), key=lambda a: a.published, reverse=True)


def fetch_source(source: dict, since: datetime, limit: int = 40) -> tuple[list[Article], dict]:
    started = time.time()
    health = {
        "id": source["id"],
        "name": source["name"],
        "ok": False,
        "count": 0,
        "error": "",
        "ms": 0,
    }
    try:
        data = None
        last: Exception | None = None
        for attempt in range(2):
            try:
                data = _http_get(source["url"])
                break
            except (socket.timeout, TimeoutError) as e:
                # 超时多半是被墙，重试也没用，直接放弃这家
                last = e
                break
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(1.0)
        if data is None:
            raise last or RuntimeError("抓取失败")
        arts = parse_feed(data, source, since)[:limit]
        health.update(ok=True, count=len(arts))
        return arts, health
    except urllib.error.HTTPError as e:
        health["error"] = f"HTTP {e.code}"
    except (socket.timeout, TimeoutError):
        health["error"] = "超时（该网站可能被屏蔽）"
    except Exception as e:  # noqa: BLE001
        health["error"] = f"{type(e).__name__}: {str(e)[:80]}"
    finally:
        health["ms"] = int((time.time() - started) * 1000)
    return [], health


def fetch_all(
    sources: list[dict],
    hours: int = 26,
    limit: int = 40,
    on_progress=None,
    deadline_seconds: int = 25,
) -> tuple[list[Article], list[dict]]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    enabled = [s for s in sources if s.get("enabled", True)]
    articles: list[Article] = []
    health: list[dict] = []
    done = 0

    def tick() -> None:
        if on_progress:
            try:
                on_progress(done, len(enabled))
            except Exception:  # noqa: BLE001
                pass

    pool = ThreadPoolExecutor(max_workers=6)
    futures = {pool.submit(fetch_source, s, since, limit): s for s in enabled}
    pending = set(futures)
    deadline = time.time() + deadline_seconds
    try:
        while pending:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            finished, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            for fut in finished:
                try:
                    arts, h = fut.result()
                except Exception as e:  # noqa: BLE001
                    arts, h = [], {"id": futures[fut]["id"], "name": futures[fut]["name"],
                                   "ok": False, "count": 0, "error": str(e)[:80], "ms": 0}
                articles.extend(arts)
                health.append(h)
                done += 1
                tick()
        # 超时还没返回的源，直接记失败，不让整个流程被拖住
        for fut in pending:
            src = futures[fut]
            health.append({"id": src["id"], "name": src["name"], "ok": False, "count": 0,
                           "error": "超时（网站无响应，可能是网络问题）", "ms": deadline_seconds * 1000})
            done += 1
            tick()
    finally:
        pool.shutdown(wait=False)
    health.sort(key=lambda h: (not h["ok"], h["name"]))
    return articles, health
