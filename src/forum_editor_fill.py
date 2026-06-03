"""论坛长文编辑器：正文填入、光标定位、配图预处理（东方财富 / 雪球共用）。"""

from __future__ import annotations

import asyncio
import re
import sys
import tempfile
from pathlib import Path

from forum_pack_format import body_to_plaintext, format_headline_plain, split_body_blocks

_PASTE_KEY = "Meta+V" if sys.platform == "darwin" else "Control+V"


def normalize_text(text: str) -> str:
    return re.sub(r"[，,。！？!?、\s]", "", text or "")


def dedupe_body_paragraphs(paras: list[str]) -> list[str]:
    """去掉与上一段高度重复的超短补充句（如 concept 摘要重复 narration）。"""
    out: list[str] = []
    for para in paras:
        p = para.strip()
        if not p:
            continue
        if not out:
            out.append(p)
            continue
        prev = out[-1]
        pn = normalize_text(p)
        prev_n = normalize_text(prev)
        if len(pn) < 28 and (
            pn in prev_n
            or prev_n.startswith(pn[: min(10, len(pn))])
            or (pn[: min(6, len(pn))] in prev_n and len(pn) < len(prev_n) * 0.65)
        ):
            continue
        out.append(p)
    return out


def concept_redundant(concept: str, narration: str) -> bool:
    c = normalize_text(concept)
    n = normalize_text(narration)
    if not c:
        return True
    if c in n:
        return True
    if len(c) <= 24 and c[: min(8, len(c))] in n:
        return True
    return False


def prepare_image_upload(image_path: str) -> str:
    """编辑器常对 PNG 伪装成 .jpg 上传失败，统一转为 JPEG。"""
    path = Path(image_path)
    try:
        from PIL import Image
    except ImportError:
        return image_path
    try:
        with Image.open(path) as im:
            if im.format == "JPEG" and path.suffix.lower() in {".jpg", ".jpeg"}:
                return image_path
            rgb = im.convert("RGB")
            tmp = Path(tempfile.gettempdir()) / f"aivideo_forum_{path.stem}.jpg"
            rgb.save(tmp, "JPEG", quality=92)
            return str(tmp)
    except OSError:
        return image_path


async def grant_clipboard(page) -> None:
    try:
        await page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    except Exception:
        pass


async def move_cursor_to_end(page) -> None:
    await page.evaluate(
        """
        () => {
          const root = document.querySelector('.ProseMirror');
          if (!root) return;
          root.focus();
          const range = document.createRange();
          range.selectNodeContents(root);
          range.collapse(false);
          const sel = window.getSelection();
          if (!sel) return;
          sel.removeAllRanges();
          sel.addRange(range);
        }
        """
    )


async def focus_editor_end(page) -> None:
    editor = page.locator(".ProseMirror.cfh_editor_area, .ProseMirror").first
    await editor.wait_for(state="attached", timeout=30_000)
    await editor.scroll_into_view_if_needed()
    await editor.click(timeout=10_000, force=True)
    await move_cursor_to_end(page)


async def clear_editor(page) -> None:
    ok = await page.evaluate(
        """
        () => {
          const root = document.querySelector('.ProseMirror.cfh_editor_area')
            || document.querySelector('.ProseMirror');
          if (!root) return false;
          root.focus();
          return true;
        }
        """
    )
    if not ok:
        raise RuntimeError("未找到正文编辑器")
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")
    await asyncio.sleep(0.2)
    await move_cursor_to_end(page)


async def paste_text(page, text: str) -> None:
    if not text.strip():
        return
    await grant_clipboard(page)
    await page.evaluate(
        "async (t) => { await navigator.clipboard.writeText(t); }",
        text,
    )
    await move_cursor_to_end(page)
    await page.keyboard.press(_PASTE_KEY)
    await asyncio.sleep(0.35)
    await move_cursor_to_end(page)


async def paste_paragraphs(page, paragraphs: list[str]) -> None:
    """逐段粘贴，确保编辑器保留段落边界。"""
    for para in paragraphs:
        text = para.strip()
        if not text:
            continue
        await focus_editor_end(page)
        await paste_text(page, text)
        await page.keyboard.press("Enter")
        await asyncio.sleep(0.15)


async def paste_body_blocks(page, body: str) -> None:
    for block in split_body_blocks(body):
        if "\n" in block:
            lines = [ln.strip() for ln in block.splitlines() if ln.strip() and ln.strip() != "---"]
            await paste_paragraphs(page, lines)
        else:
            await paste_paragraphs(page, [block])


def strip_eastmoney_cta(text: str) -> str:
    """财富号合规：去掉视频口播里的互动引导。"""
    text = re.sub(r"如果是你[^。！？]*[。！？]?", "", text)
    text = re.sub(r"觉得有用[^。！？]*[。！？]?", "", text)
    text = re.sub(r"评论区[^。！？]*[。！？]?", "", text)
    text = re.sub(r"点赞[^。！？]*[。！？]?", "", text)
    text = re.sub(r"关注[^。！？]*更新[^。！？]*[。！？]?", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def format_eastmoney_paragraph(text: str) -> str:
    """财富号 1.2：段首空两格，段内不换行。"""
    flat = re.sub(r"\s+", "", text.strip())
    if not flat:
        return ""
    return "\u3000\u3000" + flat


def format_eastmoney_block(headline: str, body: str) -> str:
    body = strip_eastmoney_cta(body_to_plaintext(body))
    parts: list[str] = []
    if headline.strip():
        parts.append(format_eastmoney_paragraph(format_headline_plain(headline)))
    for block in split_body_blocks(body):
        if "\n" in block:
            for ln in block.splitlines():
                ln = ln.strip()
                if ln and ln != "---":
                    parts.append(format_eastmoney_paragraph(ln))
        else:
            para = format_eastmoney_paragraph(block)
            if para:
                parts.append(para)
    return "\n".join(parts)


def format_eastmoney_caption(text: str) -> str:
    flat = re.sub(r"\s+", "", text.strip())
    if not flat:
        return ""
    return "\u3000\u3000" + flat


def tweak_eastmoney_text(text: str, pack_dir: Path, salt: int = 0) -> str:
    """略改正文用词，降低与历史稿件重复命中（2.5）。"""
    tweaks = [
        ("这", "这一"),
        ("可", "但"),
        ("所以", "因此"),
        ("其实", "实际上"),
        ("现在", "眼下"),
        ("已经", "已"),
        ("可能", "或许"),
    ]
    start = (sum(ord(c) for c in pack_dir.name) + salt) % len(tweaks)
    for i in range(len(tweaks)):
        src, dst = tweaks[(start + i) % len(tweaks)]
        if src in text and len(text) > 20:
            return text.replace(src, dst, 1)
    return text


async def fill_eastmoney_body_sections(
    page,
    sections: list[dict],
    *,
    pack_dir: Path,
    disclaimer: str = "",
    insert_image,
) -> None:
    await page.locator(".ProseMirror.cfh_editor_area, .ProseMirror").first.wait_for(
        state="attached", timeout=30_000
    )
    await clear_editor(page)

    wrote = False
    for idx, sec in enumerate(sections):
        headline = tweak_eastmoney_text((sec.get("headline") or "").strip(), pack_dir, idx * 3)
        body = tweak_eastmoney_text((sec.get("body") or "").strip(), pack_dir, idx * 3 + 1)
        block = format_eastmoney_block(headline, body)
        if block:
            paras = [p for p in block.split("\n") if p.strip()]
            if paras:
                await focus_editor_end(page)
                if wrote:
                    await page.keyboard.press("Enter")
                await paste_paragraphs(page, paras)
                wrote = True

        img = sec.get("image")
        if img:
            await move_cursor_to_end(page)
            await insert_image(page, prepare_image_upload(img))
            wrote = True

        caption = (sec.get("caption") or "").strip()
        if caption:
            await focus_editor_end(page)
            if wrote:
                await page.keyboard.press("Enter")
            await paste_text(page, format_eastmoney_caption(caption))
            wrote = True

    if disclaimer.strip():
        await focus_editor_end(page)
        if wrote:
            await page.keyboard.press("Enter")
        await paste_text(page, format_eastmoney_paragraph(disclaimer.strip()))

    await asyncio.sleep(0.5)


async def fill_body_sections(
    page,
    sections: list[dict],
    *,
    disclaimer: str = "",
    insert_image,
) -> None:
    await page.locator(".ProseMirror.cfh_editor_area, .ProseMirror").first.wait_for(
        state="attached", timeout=30_000
    )
    await clear_editor(page)

    wrote = False
    for sec in sections:
        headline = format_headline_plain((sec.get("headline") or "").strip())
        body = body_to_plaintext((sec.get("body") or "").strip())

        if headline:
            await focus_editor_end(page)
            if wrote:
                await page.keyboard.press("Enter")
            await paste_paragraphs(page, [headline])
            wrote = True

        if body:
            await focus_editor_end(page)
            await paste_body_blocks(page, body)
            wrote = True

        img = sec.get("image")
        if img:
            await move_cursor_to_end(page)
            await insert_image(page, prepare_image_upload(img))
            wrote = True

        caption = (sec.get("caption") or "").strip()
        if caption:
            await focus_editor_end(page)
            await paste_paragraphs(page, [caption])
            wrote = True

    if disclaimer.strip():
        await focus_editor_end(page)
        if wrote:
            await page.keyboard.press("Enter")
        await paste_text(page, disclaimer.strip())

    await asyncio.sleep(0.5)
