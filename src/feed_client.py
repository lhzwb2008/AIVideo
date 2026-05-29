"""固定信息源抓取：比全网搜索更适合每日 24h 热点。

目前内置：
- 旧版固定信息源（默认主流程已切到 Exa Search，本模块仅作兜底/调试）

输出统一 article candidate dict，供 research.py 后续评审/深读/改编。
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
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


def _rsshub_base() -> str:
    """RSSHub 可选自建/公共实例；为空时跳过依赖 RSSHub 的路由。"""
    import os

    return os.environ.get("RSSHUB_BASE_URL", "").strip().rstrip("/")


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


def _rss_text(node: ET.Element, name: str) -> str:
    child = node.find(name)
    if child is not None and child.text:
        return child.text
    for item in node:
        if item.tag.rsplit("}", 1)[-1] == name and item.text:
            return item.text
    return ""


def _parse_rss_date(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return _iso_date()
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError):
        return raw[:10] if re.match(r"\d{4}-\d{2}-\d{2}", raw) else _iso_date()


def fetch_rss(
    url: str,
    *,
    site: str,
    language: str,
    source_type: str,
    limit: int = 30,
) -> list[dict[str, Any]]:
    text = _fetch(url)
    root = ET.fromstring(text)
    nodes = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    items: list[dict[str, Any]] = []
    for node in nodes[:limit]:
        title = _rss_text(node, "title")
        link = _rss_text(node, "link")
        if not link:
            link_node = node.find("{http://www.w3.org/2005/Atom}link")
            link = link_node.attrib.get("href", "") if link_node is not None else ""
        summary = (
            _rss_text(node, "description")
            or _rss_text(node, "summary")
            or _rss_text(node, "content")
        )
        published = _rss_text(node, "pubDate") or _rss_text(node, "published") or _rss_text(node, "updated")
        if not title or not link:
            continue
        items.append(
            _candidate(
                title=title,
                url=link,
                site=site,
                published_at=_parse_rss_date(published),
                summary=summary or title,
                language=language,
                source_type=source_type,
            )
        )
    return _dedup(items)


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


FINANCE_RSS_SOURCES = [
    # 直接可用源
    ("36kr.com", "https://36kr.com/feed", "zh", "feed:36kr"),
    ("seekingalpha.com", "https://seekingalpha.com/market_currents.xml", "en", "feed:seeking-alpha"),
]

RSSHUB_FINANCE_ROUTES = [
    # 需要 RSSHUB_BASE_URL，例如 https://rsshub.app 或自建实例。
    ("wallstreetcn.com", "/wallstreetcn/hot/day", "zh", "feed:wallstreetcn-hot"),
    ("wallstreetcn.com", "/wallstreetcn/live/us-stock", "zh", "feed:wallstreetcn-us-stock"),
    ("wallstreetcn.com", "/wallstreetcn/live/hk-stock", "zh", "feed:wallstreetcn-hk-stock"),
    ("sina.com.cn", "/sina/finance/roll", "zh", "feed:sina-finance"),
    ("reuters.com", "/reuters/business/finance", "en", "feed:reuters-finance"),
    ("reuters.com", "/reuters/technology", "en", "feed:reuters-tech"),
    ("yahoo.com", "/yahoo/news/en-US/finance", "en", "feed:yahoo-finance"),
]

# A股「爆品」专用 RSSHub 路由：以散户最关注、最易引爆话题的源为主。
# 同样需要 RSSHUB_BASE_URL；为空时整体跳过。
RSSHUB_ASTOCK_ROUTES = [
    # 财联社电报：A股 24h 异动/涨停/突发最快的源（爆点首选）。
    ("cls.cn", "/cls/telegraph", "zh", "feed:cls-telegraph"),
    ("cls.cn", "/cls/depth/1000", "zh", "feed:cls-depth"),  # 头条深度
    ("cls.cn", "/cls/hot", "zh", "feed:cls-hot"),  # 热门
    # 华尔街见闻 A股 实时快讯。
    ("wallstreetcn.com", "/wallstreetcn/live/a-stock", "zh", "feed:wallstreetcn-a-stock"),
    # 东方财富：散户人气最高，自带热门个股话题性。
    ("eastmoney.com", "/eastmoney/news/cjpl", "zh", "feed:eastmoney-cjpl"),  # 财经评论
    # 新浪财经 A股 滚动。
    ("sina.com.cn", "/sina/finance/stock", "zh", "feed:sina-stock"),
]


def fetch_finance_feeds(hours: int = 24) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    sources = list(FINANCE_RSS_SOURCES)
    rsshub = _rsshub_base()
    if rsshub:
        sources.extend(
            (site, f"{rsshub}{route}", language, source_type)
            for site, route, language, source_type in RSSHUB_FINANCE_ROUTES + RSSHUB_ASTOCK_ROUTES
        )
    else:
        print("  ℹ️  未设置 RSSHUB_BASE_URL，跳过华尔街见闻/财联社/东财/Reuters/Yahoo 等 RSSHub 财经源")
    for site, url, language, source_type in sources:
        try:
            got = fetch_rss(url, site=site, language=language, source_type=source_type)
            print(f"  📰 财经源 {site} → {len(got)} 条")
            items.extend(got)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️  财经信息源抓取失败 {site}: {exc}")
            continue
    return _dedup(items)


def fetch_feed_candidates(hours: int = 24) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for fetcher in (fetch_aibase, fetch_ai_news, fetch_finance_feeds):
        try:
            items.extend(fetcher(hours=hours))
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️  信息源抓取失败 {fetcher.__name__}: {exc}")
            continue
    return _dedup(items)
