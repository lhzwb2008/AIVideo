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


def _strip_cta(text: str) -> str:
    t = text.strip()
    for pat in (
        r"觉得有用点个关注!?\s*$",
        r"评论区聊聊[^。]*。?\s*$",
        r"那你觉得[^。]*评论区[^。]*。?\s*$",
        r"那你觉得[^。]*[？?]\s*$",
    ):
        t = re.sub(pat, "", t).strip()
    return t


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
    title = (script.get("title") or "未命名").strip()
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
        n = _strip_cta(slide.get("narration") or "")
        if h:
            lines.append(f"## {h}")
            lines.append("")
        if n:
            lines.append(n)
            lines.append("")
        if i <= len(image_paths):
            lines.append(f"**【插入配图 {i}】** `images/{i:02d}.jpg`")
            lines.append("")

    lines.extend(["---", "", DISCLAIMER, ""])

    post_md = out_dir / "post.md"
    post_md.write_text("\n".join(lines), encoding="utf-8")

    img_lines = "\n".join(f"- images/{p.name}" for p in image_paths) or "- （无）"
    cover_lines = []
    if cover_path:
        cover_lines.append("- `cover.jpg`（竖封面 / 默认上传）")
    if landscape_path:
        cover_lines.append(
            "- `cover_landscape.jpg`（16:9 横封面，雪球首页推荐裁剪用）"
        )
    cover_block = "\n".join(cover_lines) if cover_lines else "- （未生成）"
    readme = f"""# 论坛图文 · {video_path.name}

与视频 `{video_path.name}` 同级目录下的同名文件夹；归档后会与 mp4 一起进入 `archive/published/日期/`。

1. 封面：
{cover_block}
2. 正文：`post.md` 全文复制，第一行标题剪切到标题框
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
