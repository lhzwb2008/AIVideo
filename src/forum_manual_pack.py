"""生成股市论坛手动发文包：与视频同目录的同名文件夹 post.md + images/ + cover.jpg + cover_landscape.jpg。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from paths import ROOT

DISCLAIMER = (
    "【风险提示】以上内容仅供学习交流，不构成任何投资建议。"
    "市场有风险，投资需谨慎。"
    "文中数据与观点仅供参考，请独立判断。"
)

# 财富号/雪球：弱化标题党、连板炒作等表述
_FORUM_TITLE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (r"五连板", "连续上涨"),
    (r"为什么能连续上涨", "近期为何表现强势"),
    (r"单日暴跌", "单日大幅回调"),
    (r"为啥", "为何"),
)

# 正文：互动引流、荐股式结尾（东方财富 2.1）
_CTA_PATTERNS: tuple[str, ...] = (
    r"觉得有用就?点赞收藏[，,、]?关注我们[^。！？?]*[。！？?]?\s*$",
    r"点赞收藏[，,、]?关注我们[^。！？?]*[。！？?]?\s*$",
    r"关注我们[，,、]?每天[^。！？?]*[。！？?]?\s*$",
    r"觉得有用点个关注!?\s*$",
    r"评论区聊聊[^。！？?]*[。！？?]?\s*$",
    r"那你觉得[^。！？?]*评论区[^。！？?]*[。！？?]?\s*$",
    r"那么问题来了[：:]?[^。！？?]*你看好[^。！？?]*[。！？?]?\s*$",
    r"那问题来了[：:]?[^。！？?]*(真行情|凑热闹|五连板)[^。！？?]*[。！？?]?\s*$",
    r"你觉得[^。！？?]*(涌向|流向)哪个(方向|板块)[？?]\s*$",
    r"你觉得[^。！？?]*[？?]\s*$",
    r"那你觉得[^。！？?]*[？?]\s*$",
    r"问题来了[：:]?[^。！？?]*[？?]\s*$",
)

# 正文：点名个股 + 极端涨跌幅（东方财富 2.2 / 2.4）
_FORUM_NARRATION_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (
        r"有只煤炭股一口气连续五个交易日涨停，一周累计涨了61%！这是啥概念？就好比一家网红奶茶店连续五天爆单到打烊都排不上号。它叫华电能源，这周的明星。",
        "部分煤炭股却明显强于指数，个别标的短期涨幅较大。同一市场里，个股走势分化很大。就好比一条美食街上，有的店门庭若市，有的店冷冷清清。",
    ),
    (
        r"一边是26只股票涨超30%，地产股香江控股也走出五连板；另一边呢，有76只股票直接跌超20%，其中朗信电气一周跌了45%，几乎腰斩。",
        "一边是个别标的涨幅靠前，另一边也有大量个股明显回调，跌幅超过两成的不在少数。",
    ),
    (
        r"这周542家公司被调研，神工股份最火，55家机构同一周排队上门看账本。机构调研就好比一群投资人专门上门翻这家店的账本，看看值不值得长期关注。",
        "这周五百多家公司接受机构调研，部分半导体、制造类公司关注度较高。机构调研就好比投资人专门翻阅企业资料，评估长期价值。需注意的是，短期涨幅较大的标的往往波动也大，追高风险不容忽视。",
    ),
    (r"中芯国际跌了近9%", "部分龙头芯片股跌幅明显"),
    (r"公共事业", "公用事业"),
    (r"EBITA", "EBITDA"),
    (
        r"雷神科技盘中直接冲到30%涨停",
        "部分AI PC概念股盘中涨幅明显",
    ),
    (
        r"今年累计涨了793%的8倍大牛股利通电子，却来了个一字跌停",
        "部分前期涨幅较大的高位标的，同日也出现明显回调",
    ),
)


def forum_dir_for_video(video_path: Path) -> Path:
    """与 mp4 同级、同名文件夹，如 output/20260531_193024/"""
    return video_path.parent / video_path.stem


def _load_script(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    script = data.get("script") or data
    if not isinstance(script, dict):
        raise ValueError(f"无效脚本: {path}")
    return script


def _sanitize_forum_title(title: str) -> str:
    t = title.strip()
    for pat, repl in _FORUM_TITLE_REPLACEMENTS:
        t = re.sub(pat, repl, t)
    return t.strip()


def _sanitize_forum_narration(text: str) -> str:
    t = text.strip()
    for pat, repl in _FORUM_NARRATION_REPLACEMENTS:
        t = re.sub(pat, repl, t)
    return t.strip()


def _strip_cta(text: str) -> str:
    t = text.strip()
    changed = True
    while changed and t:
        changed = False
        for pat in _CTA_PATTERNS:
            new_t = re.sub(pat, "", t).strip()
            if new_t != t:
                t = new_t
                changed = True
                break
    return t


def _prepare_forum_narration(text: str) -> str:
    return _strip_cta(_sanitize_forum_narration(text))


def _label_covered(label: str, narration: str) -> bool:
    label = label.strip()
    if not label or label in narration:
        return True
    core = label
    for sep in ("=", "＝", "：", ":"):
        if sep in core:
            core = core.split(sep, 1)[0].strip()
            break
    chunks = re.findall(r"[\u4e00-\u9fff]{3,}", core)
    if not chunks:
        return core in narration
    hits = sum(1 for chunk in chunks if chunk in narration)
    return hits >= max(1, len(chunks) - 1)


def _label_to_sentence(label: str) -> str:
    label = _prepare_forum_narration(label.strip())
    if not label:
        return ""
    if label.endswith(("。", "！", "？")):
        return label
    for sep in ("=", "＝", "：", ":"):
        if sep in label:
            key, val = label.split(sep, 1)
            key, val = key.strip(), val.strip()
            if key and val and len(val) >= 2:
                return f"{key}方面，大致对应{val.rstrip('。')}。"
    return ""


def _expand_from_labels(labels: list[str], narration: str) -> str:
    sentences: list[str] = []
    for lb in labels:
        if _label_covered(lb, narration):
            continue
        sent = _label_to_sentence(lb)
        if sent and sent not in narration and sent not in sentences:
            sentences.append(sent)
    if not sentences:
        return ""
    if len(sentences) == 1:
        return sentences[0]
    return "补充几个要点：" + "".join(sentences)


def _expand_forum_section(slide: dict) -> str:
    narration = _prepare_forum_narration(str(slide.get("narration") or ""))
    labels = [str(x).strip() for x in (slide.get("on_image_text") or []) if str(x).strip()]
    parts: list[str] = []

    if narration:
        parts.append(narration)

    extra = _expand_from_labels(labels, "\n".join(parts))
    if extra:
        parts.append(extra)

    concept = _prepare_forum_narration(str(slide.get("concept") or ""))
    headline = str(slide.get("headline") or "").strip()
    if (
        concept
        and concept not in "\n".join(parts)
        and concept != headline
        and len(concept) >= 8
        and not narration.startswith(concept[: min(6, len(concept))])
    ):
        parts.append(concept.rstrip("。") + "。")

    return "\n\n".join(p for p in parts if p.strip())


def _extract_video_frames(video: Path, dest_dir: Path, count: int) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dur_s = 20.0 * count
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video),
            ],
            text=True,
        )
        dur_s = float(out.strip())
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        pass

    paths: list[Path] = []
    for i in range(count):
        t = dur_s * (i + 0.5) / max(count, 1)
        out = dest_dir / f"{i + 1:02d}.jpg"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{t:.2f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(out),
            ],
            capture_output=True,
            check=False,
        )
        if out.is_file():
            paths.append(out)
    return paths


def _copy_slide_images(script: dict, dest_dir: Path, video: Path, n_slides: int) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    slides = script.get("slides") or []
    copied: list[Path] = []
    for i, slide in enumerate(slides[:n_slides], start=1):
        rel = slide.get("image_path") or slide.get("cover_image")
        if rel:
            src = ROOT / rel
            if src.is_file():
                dst = dest_dir / f"{i:02d}.jpg"
                shutil.copy2(src, dst)
                copied.append(dst)
    if len(copied) >= max(1, min(n_slides, 1)):
        return copied[:n_slides] if len(copied) >= n_slides else copied
    cover = script.get("cover_image")
    if cover:
        src = ROOT / cover
        if src.is_file():
            dst = dest_dir / "01.jpg"
            shutil.copy2(src, dst)
            copied = [dst]
    if len(copied) >= n_slides:
        return copied[:n_slides]
    if video.is_file():
        extracted = _extract_video_frames(video, dest_dir, n_slides)
        if extracted:
            return extracted
    thumb = ROOT / "logs/youtube_thumbs" / f"{video.stem}_frame0.jpg"
    if thumb.is_file():
        dst = dest_dir / "01.jpg"
        shutil.copy2(thumb, dst)
        return [dst]
    return copied


def _save_cover_jpg(src: Path, cover_dst: Path) -> bool:
    if src.suffix.lower() in {".jpg", ".jpeg"}:
        shutil.copy2(src, cover_dst)
        return cover_dst.is_file()
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-q:v", "2", str(cover_dst)],
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and cover_dst.is_file()


def _write_cover(script_path: Path, video: Path, out_dir: Path) -> tuple[Path | None, Path | None]:
    """论坛竖封面 + 源图路径（供横封面裁剪）。竖图 = 视频开场，不用正文配图。"""
    from publish_resolve import resolve_cover_image

    src = resolve_cover_image(script_path, video)
    if not src or not src.is_file():
        return None, None
    cover_dst = out_dir / "cover.jpg"
    if not _save_cover_jpg(src, cover_dst):
        return None, src
    return cover_dst, src


def _quote_block(text: str) -> str:
    return f"```\n{text.rstrip()}\n```\n\n"


def _build_readme_publish_sections(script: dict, forum_body: str) -> str:
    """各平台发布用标题/标签/简介，写入 README 便于归档后一键复制。"""
    from douyin_caption import build_sau_fields
    from social_caption import build_social_fields
    from tiktok_caption import build_tiktok_fields
    from youtube_caption import build_youtube_fields

    dy = build_sau_fields(script)
    xhs = build_social_fields(script, "xiaohongshu")
    tx = build_social_fields(script, "tencent")
    yt = build_youtube_fields(script)
    tk = build_tiktok_fields(script)

    dy_tags = (dy.get("tags") or "").strip()
    dy_hashtags = " ".join(f"#{t.strip()}" for t in dy_tags.split(",") if t.strip())
    dy_desc = (dy.get("desc") or "").strip()
    if dy_hashtags:
        dy_desc = f"{dy_desc}\n\n{dy_hashtags}".strip()

    raw_hashtags = script.get("hashtags") or []
    forum_tags = "、".join(
        str(t).strip() for t in raw_hashtags if str(t).strip()
    )
    if not forum_tags:
        forum_tags = dy_tags.replace(",", "、")

    sections: list[str] = ["## 发布文案（可直接复制）\n"]

    forum_title = _sanitize_forum_title(str(script.get("title") or dy["title"]).strip())
    forum_bits = [
        f"**标题**\n\n{_quote_block(forum_title)}",
    ]
    if forum_tags:
        forum_bits.append(f"**标签 / 话题**\n\n{_quote_block(forum_tags)}")
    if forum_body.strip():
        forum_bits.append(
            f"**正文**（不含标题行；配图位置见 **【插入配图 N】**）\n\n"
            f"{_quote_block(forum_body)}"
        )
    sections.append(
        "### 论坛图文（雪球 / 东方财富）\n\n"
        + "".join(forum_bits)
        + "上传 `cover.jpg`；雪球首页推荐位可用 `cover_landscape.jpg`。\n"
    )

    sections.append(
        "### 抖音\n\n"
        f"**标题**\n\n{_quote_block(dy['title'])}"
        f"**标签**（逗号分隔，发布时选话题）\n\n{_quote_block(dy_tags)}"
        f"**简介 + 话题**（整段复制）\n\n{_quote_block(dy_desc)}"
    )

    xhs_tags = " ".join(f"#{t}" for t in xhs.get("tags") or [])
    sections.append(
        "### 小红书\n\n"
        f"**标题**（≤20 字）\n\n{_quote_block(xhs['title'])}"
        f"**标签**\n\n{_quote_block('、'.join(xhs.get('tags') or []))}"
        f"**正文**\n\n{_quote_block(xhs['desc'])}"
        + (f"行内话题：{xhs_tags}\n\n" if xhs_tags else "")
    )

    tx_tags = "、".join(tx.get("tags") or [])
    sections.append(
        "### 视频号\n\n"
        f"**短标题**（6–16 字）\n\n{_quote_block(tx['short_title'])}"
        f"**描述**\n\n{_quote_block(tx['desc'])}"
        f"**标签**\n\n{_quote_block(tx_tags)}"
    )

    yt_tags = ", ".join(yt.get("tags") or [])
    yt_hash = " ".join(f"#{t}" for t in yt.get("tags") or [])
    yt_desc = (yt.get("description") or "").strip()
    if yt_hash and yt_hash not in yt_desc:
        yt_desc = f"{yt_desc}\n\n{yt_hash}".strip()
    sections.append(
        "### YouTube Shorts\n\n"
        f"**标题**\n\n{_quote_block(yt['title'])}"
        f"**标签**\n\n{_quote_block(yt_tags)}"
        f"**描述**\n\n{_quote_block(yt_desc)}"
    )

    sections.append(
        "### TikTok\n\n"
        f"**文案**（含 # 话题，整段复制到 App）\n\n{_quote_block(tk['title'])}"
    )

    return "\n".join(sections)


def _write_landscape_cover(src: Path, out_dir: Path) -> Path | None:
    """16:9 横封面（雪球首页推荐等），从竖封面居中偏上裁剪。"""
    if not src.is_file():
        return None
    try:
        from PIL import Image
    except ImportError:
        return None

    out_w = max(640, int(os.environ.get("AIVIDEO_FORUM_LANDSCAPE_W", "1280")))
    out_h = max(360, int(os.environ.get("AIVIDEO_FORUM_LANDSCAPE_H", "720")))
    try:
        focus_y = float(os.environ.get("AIVIDEO_FORUM_LANDSCAPE_FOCUS_Y", "0.38"))
    except ValueError:
        focus_y = 0.38
    focus_y = max(0.0, min(1.0, focus_y))

    img = Image.open(src).convert("RGB")
    w, h = img.size
    target_ratio = out_w / out_h
    if w / h >= target_ratio:
        new_h = h
        new_w = int(h * target_ratio)
        x0 = (w - new_w) // 2
        y0 = 0
    else:
        new_w = w
        new_h = int(w / target_ratio)
        x0 = 0
        y0 = int((h - new_h) * focus_y)
    crop = img.crop((x0, y0, x0 + new_w, y0 + new_h))
    if crop.size != (out_w, out_h):
        crop = crop.resize((out_w, out_h), Image.LANCZOS)

    dst = out_dir / "cover_landscape.jpg"
    crop.save(dst, "JPEG", quality=92)
    return dst if dst.is_file() else None


def build_forum_pack(
    script_path: Path,
    video_path: Path,
    out_dir: Path | None = None,
) -> dict:
    script = _load_script(script_path)
    title = _sanitize_forum_title((script.get("title") or "未命名").strip())
    slides = script.get("slides") or []
    out_dir = out_dir or forum_dir_for_video(video_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    images_dir = out_dir / "images"

    image_paths = _copy_slide_images(script, images_dir, video_path, len(slides) or 4)
    cover_path, cover_src = _write_cover(script_path, video_path, out_dir)
    landscape_src = cover_src or cover_path
    landscape_path = (
        _write_landscape_cover(landscape_src, out_dir) if landscape_src else None
    )

    lines = [f"# {title}", ""]
    for i, slide in enumerate(slides, start=1):
        h = (slide.get("headline") or "").strip()
        body = _expand_forum_section(slide)
        if h:
            lines.append(f"## {h}")
            lines.append("")
        if body:
            lines.append(body)
            lines.append("")
        if i <= len(image_paths):
            lines.append(f"**【插入配图 {i}】** `images/{i:02d}.jpg`")
            lines.append("")

    lines.extend(["---", "", DISCLAIMER, ""])

    post_md = out_dir / "post.md"
    post_text = "\n".join(lines)
    post_md.write_text(post_text, encoding="utf-8")

    # post.md 首行为 # 标题，论坛正文从第二段起复制
    forum_body = post_text
    if forum_body.startswith("# "):
        forum_body = re.sub(r"^# [^\n]+\n+", "", forum_body, count=1)

    img_lines = "\n".join(f"- images/{p.name}" for p in image_paths) or "- （无）"
    cover_lines = []
    if cover_path:
        cover_lines.append("- `cover.jpg`（竖封面 / 默认上传）")
    if landscape_path:
        cover_lines.append(
            "- `cover_landscape.jpg`（16:9 横封面，雪球首页推荐裁剪用）"
        )
    cover_block = "\n".join(cover_lines) if cover_lines else "- （未生成）"
    publish_sections = _build_readme_publish_sections(script, forum_body)
    readme = f"""# 发布素材 · {video_path.name}

与视频 `{video_path.name}` 同级目录下的同名文件夹；归档后会与 mp4 一起进入 `archive/published/日期/`。

{publish_sections}

---

## 素材清单

1. 封面：
{cover_block}
2. 论坛排版正文：`post.md`（与上方「论坛图文」正文一致，含 Markdown 标题行）
3. 配图：见 **【插入配图 N】**，上传 `images/0N.jpg`

{img_lines}

脚本：`{script_path.name}`
"""
    (out_dir / "README.md").write_text(readme, encoding="utf-8")

    return {
        "title": title,
        "out_dir": str(out_dir),
        "post_md": str(post_md),
        "cover": str(cover_path) if cover_path else "",
        "cover_landscape": str(landscape_path) if landscape_path else "",
        "images": [str(p) for p in image_paths],
        "video": str(video_path),
    }


# 兼容旧引用
forum_out_dir_for_video = forum_dir_for_video
