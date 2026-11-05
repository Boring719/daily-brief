"""把同一件事的多家报道归到一起（同一语言内用文字相似度，跨语言交给 AI 归并）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .fetch import Article

STOP_EN = set(
    """the a an and or but of to in on for with at by from as is are was were be been being
    it its this that these those he she they them his her their we our you your i me my
    not no nor so than then there here which who whom whose what when where why how
    after before over under amid amidst into onto out up down about against between during
    says said say will would can could may might must should has have had do does did
    more most less least very just also new news update live latest report reports""".split()
)

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_LATIN = re.compile(r"[a-z0-9][a-z0-9''-]*")


def tokens(text: str) -> set[str]:
    text = text.lower()
    latin = {w for w in _LATIN.findall(text) if len(w) > 2 and w not in STOP_EN}
    grams: set[str] = set()
    for seg in _CJK.findall(text):
        if len(seg) == 1:
            grams.add(seg)
            continue
        for i in range(len(seg) - 1):
            grams.add(seg[i : i + 2])
    return latin | grams


def similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter < 2:
        return 0.0
    union = len(a | b)
    jaccard = inter / union
    containment = inter / min(len(a), len(b))
    return max(jaccard, containment * 0.82)


@dataclass
class Event:
    articles: list[Article]
    zh_title: str = ""
    importance: int = 3
    source_ids: list[str] = field(default_factory=list)

    @property
    def primary(self) -> Article:
        return max(self.articles, key=lambda a: a.published)

    @property
    def n_sources(self) -> int:
        return len({a.source_id for a in self.articles})

    @property
    def regions(self) -> list[str]:
        return sorted({a.region for a in self.articles})

    def snippets(self, n: int = 3, size: int = 300) -> list[str]:
        # 优先取不同来源的摘要，便于交叉核对
        picked, seen_src = [], set()
        for a in sorted(self.articles, key=lambda x: x.published, reverse=True):
            if a.source_id in seen_src:
                continue
            s = a.snippet(size)
            if s:
                picked.append(f"[{a.source_name}] {a.title} — {s}")
                seen_src.add(a.source_id)
            if len(picked) >= n:
                break
        if not picked:
            picked = [f"[{a.source_name}] {a.title}" for a in self.articles[:n]]
        return picked


class _DSU:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def cluster_articles(articles: list[Article], threshold: float = 0.34) -> list[Event]:
    if not articles:
        return []
    toks = [tokens(a.title + " " + a.summary[:120]) for a in articles]
    dsu = _DSU(len(articles))
    for i in range(len(articles)):
        for j in range(i + 1, len(articles)):
            if similarity(toks[i], toks[j]) >= threshold:
                dsu.union(i, j)

    buckets: dict[int, list[Article]] = {}
    for i, a in enumerate(articles):
        buckets.setdefault(dsu.find(i), []).append(a)

    events = []
    for arts in buckets.values():
        arts.sort(key=lambda a: a.published, reverse=True)
        events.append(Event(articles=arts, source_ids=sorted({a.source_id for a in arts})))
    events.sort(key=lambda e: (-e.n_sources, -max(a.published.timestamp() for a in e.articles)))
    return events


def event_score(ev: Event, now_ts: float) -> float:
    """综合打分：多源确认 > 时效性 > 来源权重 > 地区多样性。"""
    tier_w = {"A": 1.0, "B": 0.8, "C": 0.6}
    weight = sum(tier_w.get(a.tier, 0.7) for a in ev.articles) / max(len(ev.articles), 1)
    newest = max(a.published.timestamp() for a in ev.articles)
    age_h = max((now_ts - newest) / 3600, 0.0)
    recency = max(0.0, 1.0 - age_h / 30.0)
    diversity = len(ev.regions) / 3.0
    return ev.n_sources * 1.6 + weight * 1.2 + recency * 1.5 + diversity * 0.8
