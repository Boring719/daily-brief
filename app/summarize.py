"""调用 DeepSeek 生成简报：先挑事件、再写、最后核查一遍。"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from .cluster import Event

SECTION_NAMES = ["美欧", "中东", "亚太", "经济·科技", "非洲·拉美"]

REGION_SECTION = {
    "us": "美欧",
    "europe": "美欧",
    "mideast": "中东",
    "asia": "亚太",
    "tech": "经济·科技",
    "africa": "非洲·拉美",
    "latam": "非洲·拉美",
    "global": "美欧",
}


class LLMError(RuntimeError):
    pass


USAGE = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}


def reset_usage() -> None:
    USAGE.update(calls=0, prompt_tokens=0, completion_tokens=0)


def _post(url: str, payload: dict, api_key: str, timeout: int = 180) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code == 401:
            raise LLMError("DeepSeek 密钥无效（401），请检查 secrets.json 里的 key") from e
        if e.code == 402:
            raise LLMError("DeepSeek 账户余额不足（402），请先充值") from e
        if e.code == 429:
            raise LLMError("请求过于频繁（429），稍后再试") from e
        raise LLMError(f"DeepSeek 接口返回 {e.code}：{detail}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"连不上 DeepSeek（{e.reason}）") from e


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def chat(messages: list[dict], settings: dict, api_key: str, max_tokens: int = 4000,
         temperature: float = 0.2, json_mode: bool = True) -> str:
    payload = {
        "model": settings.get("model", "deepseek-chat"),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    base = settings.get("api_base", "https://api.deepseek.com").rstrip("/")
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            data = _post(f"{base}/chat/completions", payload, api_key)
            usage = data.get("usage") or {}
            USAGE["calls"] += 1
            USAGE["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            USAGE["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            return data["choices"][0]["message"]["content"]
        except LLMError:
            raise
        except (KeyError, IndexError) as e:
            raise LLMError(f"DeepSeek 返回格式异常：{e}") from e
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 + attempt * 3)
    raise LLMError(f"调用失败：{last_err}")


CURATE_SYSTEM = """你是一名极其严谨的国际新闻编辑，服务于一份中文《每日国际简报》。
你会收到当天抓到的全部新闻标题，每行格式为：[编号] 媒体名 | 标题（可能是中文、英文、葡萄牙文、韩文）。

任务：挑出当天真正具有国际影响的事件，给出中文标题和栏目分类。

硬性规则：
1. 只做挑选、归并、起标题；不得编造任何事实。
2. 同一件事的不同来源报道必须合并成一条——包括中英文不同媒体对同一事件的报道。
   但只合并"同一时间、同一主体、同一结果"的报道。把不同事件硬凑成一条，比漏掉更糟糕。
   如果两条新闻发生在不同国家、或主体不同，即使主题相近也必须分开。
3. 必须剔除：体育赛事、娱乐明星、地方治安、社会琐事、商业软文、榜单/攻略、纯产品发布。
4. 优先保留：战争与安全、外交、选举与政局、经济与贸易、能源与气候、重大科技与 AI、公共卫生、重大灾难。
5. 地区要均衡，五个栏目（美欧 / 中东 / 亚太 / 经济·科技 / 非洲·拉美）都要有内容，每个栏目至少 2 条；非洲、拉美有大事也不要漏。
6. 宁可少而硬，不要多而水。挑选 __COUNT__ 条左右，最多 __MAX__ 条，多出来的会被丢弃。

栏目只能取以下之一：美欧 / 中东 / 亚太 / 经济·科技 / 非洲·拉美

只输出 JSON：
{"events":[{"ids":[1,2],"zh_title":"不超过28个汉字，不要夹英文","section":"美欧","importance":1到5,"region":"一句话地区说明"}]}"""


DIGEST_SYSTEM = f"""你是《每日国际简报》的主编，读者是关心世界但没时间刷新闻的普通人。
你会收到已选定的新闻事件，每个事件带编号和若干家媒体的报道片段。

请写一份中文简报。绝对的底线规则：
1. 只能使用材料里出现的?实、数字、人名、机构名。材料没有的，一律不写。
2. 不同来源说法冲突时，在 fact 里写明"有媒体称……另有媒体称……"，不要替读者下结论。
3. 不评论、不预测、不站队，只交代事实和它为何重要。
4. fact 必须具体（谁、做了什么、结果如何），禁止"引发关注""备受瞩目"这类空话。
5. 每条 item 的 event_id 必须来自输入，不能自造。
6. 中文里不要夹英文单词（OpenAI、NASA、GDP 这类专有名词/缩写除外）。找不到对应中文就换个说法，绝不保留英文原词。
7. 不要出现"材料""原文""输入"这类字眼，直接陈述事实，读者不知道你在看什么材料。
8. 一条 fact 只讲一个事件。如果写完发现里面有两个不同主体/地点的事，说明该拆开，请只保留与本条标题一致的那部分。

输出 JSON：
{{
  "one_line": "一句话概括今天的世界（不超过45字，只陈述事实）",
  "top3": [{{"title": "8-16字", "summary": "60字以内，为什么这是今天最重要的事"}}],
  "sections": [
    {{"name": "美欧",
      "items": [
        {{"event_id": 3,
          "title": "不超过24字",
          "fact": "80字以内的事实陈述",
          "why": "50字以内，为什么与普通人有关"}}
      ]}}
  ],
  "caveats": ["关于本日信息可靠性的说明，最多2条，可为空数组"]
}}

要求：每个栏目 2-4 条；sections 只包含有内容的栏目，顺序为 {" → ".join(SECTION_NAMES)}。
top3 必须从 items 里挑，不要另起炉灶。"""


VERIFY_SYSTEM = """你是事实核查员。输入是若干条简报条目，每条含编号、fact（待核查陈述）和对应媒体的原始报道片段。

逐条判断 fact 中的关键陈述是否被给出的材料支持：
- "支持"：材料能明确支撑 fact 的核心陈述
- "部分支持"：大方向对，但某个数字/细节材料里没有，或来源之间不一致
- "不支持"：材料里找不到依据，或明显矛盾

不要宽容，也不要吹毛求疵：只有差异会影响读者判断时才判"部分支持"，措辞不同不算。

note 是写给读者看的一句话（不超过 40 字），要具体说明哪里有出入，例如"BBC 称 15 架，CBS 标题称 20 架"。
严禁在 note 里出现"材料""原文""输入"等字眼。

只输出 JSON：
{"items":[{"event_id":3,"verdict":"支持","note":"若有问题，一句话说明"}]}"""


def _clean_zh(text: str) -> str:
    """把模型偶尔漏出来的英文常用词换成中文。"""
    if not text:
        return text
    fixes = {
        "reform": "改革", "reforms": "改革", "chamber": "议院", "chambers": "议院",
        "tariff": "关税", "tariffs": "关税", "sanction": "制裁", "sanctions": "制裁",
        "summit": "峰会", "veto": "否决", "impeachment": "弹劾", "ceasefire": "停火",
        "blackout": "停电", "pipeline": "管道", "subsidy": "补贴", "subsidies": "补贴",
        "inflation": "通胀", "referendum": "公投", "coalition": "联盟", "cabinet": "内阁",
    }
    for k, v in fixes.items():
        text = re.sub(rf"\b{k}\b", v, text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", text).strip()


def _fmt_time(ev: Event) -> str:
    return ev.primary.published.astimezone().strftime("%m-%d %H:%M")


def curate(articles: list, settings: dict, api_key: str, count: int) -> list[dict]:
    """让模型从全部标题里挑事件、跨语言归并、起中文标题。"""
    lines = [f"[{i}] {a.source_name} | {a.title}" for i, a in enumerate(articles)]
    prompt = CURATE_SYSTEM.replace("__COUNT__", str(count)).replace("__MAX__", str(count + 4))
    raw = chat(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "候选标题：\n" + "\n".join(lines)},
        ],
        settings,
        api_key,
        max_tokens=4000,
        temperature=0.3,
    )
    data = _extract_json(raw)
    picked = []
    used: set[int] = set()
    for item in data.get("events", []):
        ids = []
        for x in item.get("ids", []):
            try:
                idx = int(x)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(articles) and idx not in used:
                ids.append(idx)
        if not ids:
            continue
        used.update(ids)
        ev = Event(articles=sorted((articles[i] for i in ids), key=lambda a: a.published, reverse=True))
        ev.zh_title = str(item.get("zh_title") or "").strip()
        ev.importance = int(item.get("importance") or 3)
        section = item.get("section")
        if section not in SECTION_NAMES:
            section = REGION_SECTION.get(ev.primary.region, "美欧")
        picked.append({"event": ev, "section": section, "importance": ev.importance})
    picked.sort(key=lambda p: -p["importance"])
    return picked[: count + 4]


def digest(picked: list[dict], settings: dict, api_key: str, date_str: str) -> dict:
    blocks = []
    for i, p in enumerate(picked):
        ev: Event = p["event"]
        snips = "\n".join(f"  - {s}" for s in ev.snippets(3, 280))
        blocks.append(f"#{i} 栏目建议={p['section']} 中文标题参考={ev.zh_title or '(无)'}\n{snips}")
    user = f"日期：{date_str}\n\n事件材料：\n\n" + "\n\n".join(blocks)
    raw = chat(
        [{"role": "system", "content": DIGEST_SYSTEM}, {"role": "user", "content": user}],
        settings,
        api_key,
        max_tokens=4500,
        temperature=0.25,
    )
    data = _extract_json(raw)
    return _attach_sources(data, picked, date_str)


def _attach_sources(data: dict, picked: list[dict], date_str: str) -> dict:
    """把 AI 给的 event_id 还原成真实来源和链接（绝不让模型自己编 URL）。"""
    out = {
        "title": f"{date_str} 国际简报",
        "date": date_str,
        "one_line": str(data.get("one_line") or "").strip(),
        "top3": [],
        "sections": [],
        "caveats": [str(c) for c in (data.get("caveats") or []) if str(c).strip()],
    }

    def build_item(raw_item: dict) -> dict | None:
        try:
            idx = int(raw_item.get("event_id"))
        except (TypeError, ValueError):
            return None
        if not (0 <= idx < len(picked)):
            return None
        ev: Event = picked[idx]["event"]
        sources, seen = [], set()
        for a in sorted(ev.articles, key=lambda x: x.published, reverse=True):
            if a.source_id in seen:
                continue
            seen.add(a.source_id)
            sources.append({"name": a.source_name, "url": a.link, "lang": a.lang})
        conf = "多源确认" if ev.n_sources >= 2 else "单一来源"
        return {
            "title": _clean_zh(str(raw_item.get("title") or ev.zh_title or ev.primary.title).strip()),
            "fact": _clean_zh(str(raw_item.get("fact") or "").strip()),
            "why": _clean_zh(str(raw_item.get("why") or "").strip()),
            "confidence": conf,
            "n_sources": ev.n_sources,
            "time": _fmt_time(ev),
            "region": REGION_SECTION.get(ev.primary.region, "美欧"),
            "sources": sources[:6],
        }

    for sec in data.get("sections", []):
        name = sec.get("name")
        if name not in SECTION_NAMES:
            continue
        items = [it for it in (build_item(x) for x in sec.get("items", [])) if it]
        if items:
            out["sections"].append({"name": name, "items": items})

    # 排序固定，保证每次版面一致
    order = {n: i for i, n in enumerate(SECTION_NAMES)}
    out["sections"].sort(key=lambda s: order[s["name"]])

    for t in data.get("top3", [])[:3]:
        out["top3"].append(
            {
                "title": _clean_zh(str(t.get("title") or "").strip()),
                "summary": _clean_zh(str(t.get("summary") or "").strip()),
            }
        )
    all_items = [it for s in out["sections"] for it in s["items"]]
    if not out["top3"] and all_items:
        out["top3"] = [{"title": it["title"], "summary": it["why"] or it["fact"][:60]} for it in all_items[:3]]
    return out


def verify(digest_data: dict, picked: list[dict], settings: dict, api_key: str) -> dict:
    """第二道关：核对每条 fact 是否有材料支撑，不支持的直接删掉。"""
    id_map: dict[int, dict] = {}
    lines = []
    for sec in digest_data["sections"]:
        for it in sec["items"]:
            # 用 (标题+fact) 反查来源事件：attach 时保留了 n_sources，这里按标题匹配
            id_map[len(id_map)] = it
            lines.append(f"#{len(id_map) - 1} fact={it['fact']}")
    if not lines:
        return digest_data
    # 找到每个 item 对应的原始材料：按标题在 picked 里找最接近的
    materials = []
    for key, it in id_map.items():
        ev = _match_event(it, picked)
        mats = "\n".join(f"  - {s}" for s in ev.snippets(3, 240)) if ev else "  - （无材料）"
        materials.append(f"#{key}\n事实：{it['fact']}\n材料：\n{mats}")
    raw = chat(
        [
            {"role": "system", "content": VERIFY_SYSTEM},
            {"role": "user", "content": "\n\n".join(materials)},
        ],
        settings,
        api_key,
        max_tokens=2000,
        temperature=0.0,
    )
    data = _extract_json(raw)
    verdicts = {}
    for row in data.get("items", []):
        try:
            verdicts[int(row.get("event_id"))] = (row.get("verdict"), str(row.get("note") or "").strip())
        except (TypeError, ValueError):
            continue

    dropped = []
    for key, it in id_map.items():
        verdict, note = verdicts.get(key, ("支持", ""))
        if verdict == "不支持":
            dropped.append(it["title"])
            it["_drop"] = True
        elif verdict == "部分支持":
            it["confidence"] = "部分待核实"
            if note:
                it["note"] = _clean_zh(note)
        # “支持”的核查理由只写进日志，不给读者看，避免版面啰嗦
    for sec in digest_data["sections"]:
        sec["items"] = [it for it in sec["items"] if not it.get("_drop")]
    digest_data["sections"] = [s for s in digest_data["sections"] if s["items"]]
    if dropped:
        digest_data.setdefault("caveats", []).append("已剔除 " + str(len(dropped)) + " 条核查未通过的条目：" + "、".join(dropped[:3]))
    return digest_data


def _match_event(item: dict, picked: list[dict]) -> Event | None:
    title = item.get("title", "")
    best, best_score = None, 0.0
    for p in picked:
        ev: Event = p["event"]
        score = 0.0
        if ev.zh_title and (ev.zh_title[:6] in title or title[:6] in ev.zh_title):
            score = 0.9
        else:
            from .cluster import tokens, similarity

            score = similarity(tokens(title), tokens(ev.zh_title or ev.primary.title))
        if score > best_score:
            best, best_score = ev, score
    return best if best_score >= 0.25 else None


def fallback_digest(picked: list[dict], date_str: str) -> dict:
    """没有 API key 时的降级版本：只排事实，不写解读。"""
    sections: dict[str, list[dict]] = {}
    for p in picked:
        ev: Event = p["event"]
        sec = p["section"]
        sources, seen = [], set()
        for a in sorted(ev.articles, key=lambda x: x.published, reverse=True):
            if a.source_id in seen:
                continue
            seen.add(a.source_id)
            sources.append({"name": a.source_name, "url": a.link, "lang": a.lang})
        sections.setdefault(sec, []).append(
            {
                "title": ev.zh_title or ev.primary.title,
                "fact": ev.primary.snippet(120),
                "why": "",
                "confidence": "多源确认" if ev.n_sources >= 2 else "单一来源",
                "n_sources": ev.n_sources,
                "time": _fmt_time(ev),
                "region": sec,
                "sources": sources[:6],
            }
        )
    out_sections = [{"name": n, "items": sections[n]} for n in SECTION_NAMES if n in sections]
    top = [s["items"][0] for s in out_sections][:3]
    return {
        "title": f"{date_str} 国际简报",
        "date": date_str,
        "one_line": "（当前为未接入 AI 的降级版：只按多源确认度排序，没有翻译和解读）",
        "top3": [{"title": t["title"][:18], "summary": f"{t['n_sources']} 家媒体报道"} for t in top],
        "sections": out_sections,
        "caveats": ["未接入 AI：标题未翻译，内容未做事实核查。请在 secrets.json 填入 DeepSeek 密钥。"],
        "fallback": True,
    }
