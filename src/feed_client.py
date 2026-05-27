"""固定信息源抓取：比全网搜索更适合每日 24h 热点。

目前内置：
- AIbase 中文资讯页
- AI News 英文首页

输出统一 article candidate dict，供 research.py 后续评审/深读/改编。
"""

from __future__ import annotations

import html
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin


def _fetch(url: str, *, timeout: float = 45) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 AIVideoBot/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def _strip_tags(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _site_from_url(url: str) -> str:
    m = re.match(r"https?://([^/]+)/?", url or "")
    return (m.group(1) if m else "").replace("www.", "")


def _today_local() -> datetime:
    return datetime.now().astimezone()


def _iso_date(dt: datetime | None = None) -> str:
    return (dt or _today_local()).date().isoformat()


def _dedup(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        key = url if url and url != "https://www.aibase.com/zh/news" else title
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _candidate(
    *,
    title: str,
    url: str,
    site: str = "",
    published_at: str | None = None,
    summary: str = "",
    language: str,
    source_type: str,
) -> dict[str, Any]:
    title = _strip_tags(title)
    summary = _strip_tags(summary)
    return {
        "title": title,
        "url": url,
        "site": site or _site_from_url(url),
        "author": "",
        "published_at": published_at or _iso_date(),
        "language": language,
        "summary_en": summary if language == "en" else "",
        "summary_zh": summary if language == "zh" else title,
        "thesis": summary or title,
        "key_facts": [summary or title],
        "narrative_arc": "最新资讯 → 关键事实 → 行业影响",
        "heat_score": 7,
        "heat_evidence": f"{site or _site_from_url(url)} 最新更新",
        "estimated_pages": 5,
        "source_type": source_type,
    }


def fetch_aibase(hours: int = 24) -> list[dict[str, Any]]:
    """抓 AIbase 最新新闻。页面标注“刚刚”的条目天然是 24h 内。"""
    url = "https://www.aibase.com/zh/news"
    text = _fetch(url)
    plain = _strip_tags(text)

    # AIbase 页面常见结构是 “刚刚.AIbase标题摘要标题”。没有稳定 href 时先用标题+站点 URL。
    chunks = re.split(r"刚刚\s*\.\s*AIbase", plain)
    starters = (
        "Maia Chess", "在清华", "擎朗智能于", "百川智能发布", "随着全球", "乔治·霍茨指出",
        "阿里巴巴", "3D生成AI领域", "蚂蚁集团CEO", "支付宝宣布", "微软Microsoft",
        "人工智能大模型", "昆仑万维集团", "谷歌DeepMind", "YouTube科技频道",
        "微软研究院", "面壁智能联合", "OpenAI桌面代理", "海尔发布",
    )
    items: list[dict[str, Any]] = []
    for chunk in chunks[1:]:
        chunk = chunk.strip()
        if not chunk:
            continue
        # 标题后通常跟一段正文摘要；用新闻正文常见开头做边界。
        title = chunk[:80]
        for starter in starters:
            if starter in chunk:
                title = chunk.split(starter, 1)[0]
                break
        for sep in (
            " 正式发布 ", " 在", " 于", " 指出", " 宣布", " 发布", " 推出", " 开源", " 披露", " 随着",
            " 公司", " 团队", " 集团", " CEO", " 近日", " 该", " 其", " 微软", " 谷歌",
        ):
            if sep in title:
                title = title.split(sep, 1)[0]
                if sep.strip() == "正式发布":
                    title += " 正式发布"
                break
        title = title[:72]
        title = title.strip(" ，。:：")
        # 避免菜单/噪声。
        if len(title) < 10 or "首页" in title:
            continue
        summary = chunk[len(title): len(title) + 120].strip(" ，。:：")
        items.append(
            _candidate(
                title=title,
                url=url,
                site="aibase.com",
                summary=summary,
                language="zh",
                source_type="feed:aibase",
            )
        )
        if len(items) >= 30:
            break
    return _dedup(items)


def _parse_ai_news_date(raw: str) -> datetime | None:
    raw = _strip_tags(raw)
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=_today_local().tzinfo)
        except ValueError:
            pass
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


def fetch_ai_news(hours: int = 24) -> list[dict[str, Any]]:
    """抓 artificialintelligence-news.com 首页文章，按日期过滤。"""
    base = "https://www.artificialintelligence-news.com/"
    text = _fetch(base)
    now = _today_local()
    cutoff = now - timedelta(hours=hours)

    items: list[dict[str, Any]] = []
    plain = _strip_tags(text)
    date_pat = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}"
    matches = list(re.finditer(date_pat, plain))
    section_words = {
        "Marketing Tech News", "Internet of Things News", "Developer Tech News",
        "AI News", "View All Latest", "Latest", "Subscribe",
    }
    for idx, m in enumerate(matches):
        dt = _parse_ai_news_date(m.group(0))
        if dt is None or dt < cutoff:
            continue
        end = matches[idx + 1].start() if idx + 1 < len(matches) else min(len(plain), m.end() + 220)
        chunk = plain[m.end():end].strip()
        parts = re.split(
            r"\s+(?:AI in Action|Artificial Intelligence|Governance, Regulation & Policy|Developer Tech News|Marketing Tech News|Internet of Things News|Physical AI|Environment & Sustainability|Inside AI|Features)\s+",
            chunk,
        )
        title = parts[-1].strip() if parts else chunk
        title = re.sub(r"\s+", " ", title).strip(" -|")
        if len(title) < 12 or title in section_words or "Subscribe" in title:
            continue
        href = ""
        for a in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>([\s\S]{5,220}?)</a>', text, flags=re.I):
            if _strip_tags(a.group(2)) == title:
                href = a.group(1)
                break
        url = urljoin(base, href) if href else base
        items.append(
            _candidate(
                title=title,
                url=url,
                site="artificialintelligence-news.com",
                published_at=dt.date().isoformat(),
                summary=title,
                language="en",
                source_type="feed:ai-news",
            )
        )
    return _dedup(items)


def fetch_feed_candidates(hours: int = 24) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for fetcher in (fetch_aibase, fetch_ai_news):
        try:
            items.extend(fetcher(hours=hours))
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️  信息源抓取失败 {fetcher.__name__}: {exc}")
            continue
    return _dedup(items)
