"""B 站专栏（长文）发布：复用 biliup 登录 cookie，API 保存草稿 + 配图 + 浏览器提交发布。"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
from html import escape
from pathlib import Path

from eastmoney_publisher import _chrome_path, parse_forum_pack, sau_home
from forum_pack_format import body_to_html, body_to_opus_lines, split_body_blocks
from sau_client import bilibili_account, bilibili_cookie_path


class BilibiliArticleError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def bilibili_article_enabled() -> bool:
    """默认随 B 站视频发布开启；设 AIVIDEO_PUBLISH_BILIBILI_ARTICLE=0 可只发视频。"""
    if not _env("AIVIDEO_PUBLISH_BILIBILI", "0").lower() in ("1", "true", "yes", "on"):
        return False
    raw = _env("AIVIDEO_PUBLISH_BILIBILI_ARTICLE", "1")
    return raw.lower() not in ("0", "false", "no", "off")


def load_bilibili_credentials(*, root: Path | None = None, account: str | None = None) -> dict:
    path = bilibili_cookie_path(root=root)
    if not path.is_file():
        raise BilibiliArticleError(
            f"B 站账号文件不存在: {path}\n请先运行: ./bilibili-login.sh"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    cookies = {
        c["name"]: c["value"]
        for c in (data.get("cookie_info") or {}).get("cookies") or []
    }
    if "SESSDATA" not in cookies or "bili_jct" not in cookies:
        raise BilibiliArticleError(f"cookie 格式异常: {path}")
    return {
        "account": account or bilibili_account(),
        "cookie_path": path,
        "cookies": cookies,
        "cookie_header": "; ".join(f"{k}={v}" for k, v in cookies.items()),
        "csrf": cookies["bili_jct"],
    }


def _api_post(url: str, fields: dict, cred: dict) -> dict:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Cookie": cred["cookie_header"],
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://member.bilibili.com/",
            "Origin": "https://member.bilibili.com",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise BilibiliArticleError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise BilibiliArticleError(f"网络请求失败: {exc}") from exc

    if payload.get("code") != 0:
        raise BilibiliArticleError(
            f"B 站 API 错误 code={payload.get('code')}: {payload.get('message')}"
        )
    return payload


def upload_article_image(image_path: Path, cred: dict) -> str:
    """专栏配图/封面：走 creative/article/upcover。"""
    if not image_path.is_file():
        raise BilibiliArticleError(f"图片不存在: {image_path}")
    suffix = image_path.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    payload = _api_post(
        "https://api.bilibili.com/x/article/creative/article/upcover",
        {
            "cover": f"data:{mime};base64,{b64}",
            "csrf": cred["csrf"],
        },
        cred,
    )
    url = (payload.get("data") or {}).get("url")
    if not url:
        raise BilibiliArticleError(f"上传图片失败: {image_path}")
    return str(url)


def _pick_banner(pack_dir: Path) -> Path:
    for name in ("cover_landscape.jpg", "cover.jpg"):
        path = pack_dir / name
        if path.is_file():
            return path
    raise BilibiliArticleError(f"缺少 cover.jpg: {pack_dir}")


def _article_content_type() -> int:
    """0=HTML 旧编辑器；3=opus JSON（创作中心新版，默认）。"""
    return _env_int("BILIBILI_ARTICLE_TYPE", 3)


def _image_size(path: Path) -> tuple[int, int, int]:
    """返回 (width, height, file_size)。"""
    size = path.stat().st_size
    try:
        from PIL import Image

        with Image.open(path) as img:
            w, h = img.size
            return int(w), int(h), size
    except Exception:
        pass
    # JPEG 简易解析
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        try:
            raw = path.read_bytes()
            if len(raw) > 4 and raw[:2] == b"\xff\xd8":
                i = 2
                while i < len(raw) - 8:
                    marker = raw[i : i + 2]
                    if marker[0] != 0xFF:
                        break
                    if marker[1] in (0xC0, 0xC1, 0xC2):
                        h = struct.unpack(">H", raw[i + 5 : i + 7])[0]
                        w = struct.unpack(">H", raw[i + 7 : i + 9])[0]
                        return w, h, size
                    length = struct.unpack(">H", raw[i + 2 : i + 4])[0]
                    i += 2 + length
        except Exception:
            pass
    return 1280, 720, size


def _bili_img_html(url: str) -> str:
    return (
        f'<figure class="img-box" contenteditable="false">'
        f'<img src="{escape(url)}">'
        f'<figcaption class="caption" contenteditable=""></figcaption>'
        f"</figure>"
    )


def _summary_from_sections(sections: list[dict], *, max_len: int = 120) -> str:
    for sec in sections:
        body = (sec.get("body") or "").strip()
        if body:
            flat = re.sub(r"\s+", "", body)
            return flat[:max_len]
    return ""


def forum_pack_to_html(
    data: dict,
    *,
    cred: dict,
    upload_images: bool = True,
) -> tuple[str, list[str]]:
    """将论坛包转为专栏 HTML；配图上传后嵌入 <img>。"""
    parts: list[str] = []
    image_urls: list[str] = []
    uploaded_by_path: dict[str, str] = {}

    cover = data.get("cover")
    if cover and upload_images:
        cover_path = Path(cover)
        cover_url = upload_article_image(cover_path, cred)
        uploaded_by_path[str(cover_path.resolve())] = cover_url
        image_urls.append(cover_url)
        parts.append(_bili_img_html(cover_url))

    for sec in data.get("sections") or []:
        headline = (sec.get("headline") or "").strip()
        body = (sec.get("body") or "").strip()
        if headline:
            parts.append(f"<h2>{escape(headline)}</h2>")
        if body:
            parts.append(body_to_html(body))

        img = sec.get("image")
        if img and upload_images:
            path = Path(img)
            key = str(path.resolve())
            url = uploaded_by_path.get(key)
            if not url:
                url = upload_article_image(path, cred)
                uploaded_by_path[key] = url
                image_urls.append(url)
            parts.append(_bili_img_html(url))

        caption = (sec.get("caption") or "").strip()
        if caption:
            parts.append(f"<p><em>{escape(caption)}</em></p>")

    disclaimer = (data.get("disclaimer") or "").strip()
    if disclaimer:
        parts.append(f"<p><em>{escape(disclaimer)}</em></p>")

    html = "\n".join(parts).strip()
    if not html:
        raise BilibiliArticleError("专栏正文为空")
    return html, image_urls


def forum_pack_to_opus_content(
    data: dict,
    *,
    cred: dict,
    upload_images: bool = True,
) -> tuple[str, list[str]]:
    """创作中心新版：content 为 JSON 字符串（type=3）。"""
    ops: list[dict] = []
    image_urls: list[str] = []
    uploaded_by_path: dict[str, str] = {}

    def append_image(path: Path, url: str) -> None:
        w, h, size = _image_size(path)
        ops.append(
            {
                "attributes": {"class": "normal-img"},
                "insert": {
                    "native-image": {
                        "alt": "read-normal-img",
                        "url": url,
                        "width": w,
                        "height": h,
                        "size": size,
                        "status": "loaded",
                    }
                },
            }
        )
        ops.append({"insert": "\n"})

    cover = data.get("cover")
    if cover and upload_images:
        cover_path = Path(cover)
        cover_url = upload_article_image(cover_path, cred)
        uploaded_by_path[str(cover_path.resolve())] = cover_url
        image_urls.append(cover_url)
        append_image(cover_path, cover_url)

    for sec in data.get("sections") or []:
        headline = (sec.get("headline") or "").strip()
        body = (sec.get("body") or "").strip()
        if headline:
            ops.append({"insert": headline})
            ops.append({"attributes": {"header": 2}, "insert": "\n"})
        if body:
            for line in body_to_opus_lines(body):
                ops.append({"insert": line})
                ops.append({"insert": "\n"})

        img = sec.get("image")
        if img and upload_images:
            path = Path(img)
            key = str(path.resolve())
            url = uploaded_by_path.get(key)
            if not url:
                url = upload_article_image(path, cred)
                uploaded_by_path[key] = url
                image_urls.append(url)
            append_image(path, url)

        caption = (sec.get("caption") or "").strip()
        if caption:
            ops.append({"insert": caption, "attributes": {"italic": True}})
            ops.append({"insert": "\n"})

    disclaimer = (data.get("disclaimer") or "").strip()
    if disclaimer:
        ops.append({"insert": disclaimer})

    if not ops:
        raise BilibiliArticleError("专栏正文为空（opus）")
    return json.dumps({"ops": ops}, ensure_ascii=False), image_urls


def save_article_draft(
    *,
    title: str,
    html: str,
    summary: str,
    banner_url: str,
    image_urls: list[str],
    cred: dict,
    category: int | None = None,
    aid: int | None = None,
    content_type: int | None = None,
) -> int:
    category = category if category is not None else _env_int("BILIBILI_ARTICLE_CATEGORY", 4)
    ctype = content_type if content_type is not None else _article_content_type()
    words = max(1, len(re.sub(r"\s+", "", summary or html[:200])))
    joined_images = ",".join(image_urls)
    fields = {
        "title": title[:60],
        "banner_url": banner_url,
        "content": html,
        "summary": (summary or title)[:200],
        "words": str(words),
        "category": str(category),
        "list_id": "0",
        "tid": str(category),
        "reprint": "0",
        "tags": _env("BILIBILI_ARTICLE_TAGS", ""),
        "image_urls": joined_images,
        "origin_image_urls": joined_images,
        "dynamic_intro": "",
        "media_id": "0",
        "spoiler": "0",
        "original": "1",
        "type": str(ctype),
        "aid": str(aid or ""),
        "csrf": cred["csrf"],
    }
    payload = _api_post(
        "https://api.bilibili.com/x/article/creative/draft/addupdate",
        fields,
        cred,
    )
    new_aid = int((payload.get("data") or {}).get("aid") or 0)
    if not new_aid:
        raise BilibiliArticleError("保存专栏草稿失败：未返回 aid")
    return new_aid


def article_editor_url(aid: int) -> str:
    """新版 opus 编辑器（旧版 article-text 可能显示空白）。"""
    return (
        f"https://member.bilibili.com/platform/upload/text/new-edit?aid={aid}"
    )


def _playwright_storage_from_cred(cred: dict) -> dict:
    data = json.loads(Path(cred["cookie_path"]).read_text(encoding="utf-8"))
    cookies = []
    for c in (data.get("cookie_info") or {}).get("cookies") or []:
        cookies.append(
            {
                "name": c["name"],
                "value": c["value"],
                "domain": ".bilibili.com",
                "path": "/",
                "expires": int(c.get("expires") or -1),
                "httpOnly": bool(c.get("http_only")),
                "secure": bool(c.get("secure")),
                "sameSite": "Lax",
            }
        )
    return {"cookies": cookies, "origins": []}


def _ensure_patchright() -> None:
    home = sau_home()
    venv_site = home / ".venv" / "lib"
    if venv_site.is_dir():
        for sub in venv_site.iterdir():
            if sub.name.startswith("python"):
                sys.path.insert(0, str(sub / "site-packages"))
                break
    try:
        from patchright.async_api import async_playwright  # noqa: F401
    except ImportError as exc:
        raise BilibiliArticleError(
            "未安装 patchright。请先运行: ./setup-sau.sh"
        ) from exc


def _article_auto_publish_enabled() -> bool:
    return _env("BILIBILI_ARTICLE_AUTO_PUBLISH", "0").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _reuse_article_aid(pack_dir: Path, *, root: Path | None = None) -> int | None:
    explicit = _env_int("BILIBILI_ARTICLE_AID", 0)
    if explicit > 0:
        return explicit
    log_path = (root or Path.cwd()) / "logs" / "last_bilibili_publish.json"
    if not log_path.is_file():
        return None
    try:
        payload = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    article = payload.get("article") or {}
    if str(article.get("pack_dir") or "") != str(pack_dir.resolve()):
        return None
    aid = int(article.get("aid") or 0)
    return aid or None


async def _prepare_article_publish_ui(editor) -> None:
    """打开发布设置并尽量选好分类，等待编辑器校验完成。"""
    for label in ("发布设置", "设置"):
        tab = editor.locator(f"text={label}").first
        if await tab.count():
            try:
                await tab.click(timeout=5_000)
                await asyncio.sleep(1)
            except Exception:
                pass
            break
    cat_labels = ("财经", "科技", "知识", "社会", "生活")
    for label in cat_labels:
        opt = editor.locator(f"text={label}").first
        if await opt.count():
            try:
                await opt.click(timeout=3_000)
                await asyncio.sleep(0.5)
                break
            except Exception:
                continue
    try:
        await editor.locator("body").click(position={"x": 200, "y": 200}, timeout=3_000)
    except Exception:
        pass
    await asyncio.sleep(2)


async def _publish_article_via_browser_async(aid: int, cred: dict) -> None:
    _ensure_patchright()
    from patchright.async_api import async_playwright

    state_dir = sau_home() / "cookies"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "_bilibili_article_pw_state.json"
    state_path.write_text(
        json.dumps(_playwright_storage_from_cred(cred), ensure_ascii=False),
        encoding="utf-8",
    )
    headless = _env("BILIBILI_ARTICLE_BROWSER_HEADLESS", "1").lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    launch: dict = {"headless": headless}
    chrome = _chrome_path()
    if chrome:
        launch["executable_path"] = chrome

    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch)
        context = await browser.new_context(
            storage_state=str(state_path),
            locale="zh-CN",
            viewport={"width": 1440, "height": 900},
        )
        page = await context.new_page()
        try:
            await page.goto(
                article_editor_url(aid),
                wait_until="networkidle",
                timeout=120_000,
            )
            await asyncio.sleep(6)
            editor = None
            for frame in page.frames:
                if "read-editor" in (frame.url or ""):
                    editor = frame
                    break
            if editor is None:
                raise BilibiliArticleError("未找到专栏编辑器 iframe（read-editor）")
            publish_btn = editor.locator("text=发布").last
            if not await publish_btn.count():
                raise BilibiliArticleError("编辑器内未找到「发布」按钮")
            await _prepare_article_publish_ui(editor)
            enabled = False
            for _ in range(90):
                try:
                    enabled = await publish_btn.is_enabled()
                except Exception:
                    enabled = False
                if enabled:
                    break
                await asyncio.sleep(1)
            if not enabled:
                raise BilibiliArticleError(
                    "「发布」按钮长时间不可用（请检查标题/封面/分类或稍后在创作中心手动发布）"
                )
            await publish_btn.click(timeout=30_000)
            await asyncio.sleep(4)
            body = await editor.inner_text("body")
            if "已提交成功" in body or "提交成功" in body:
                return
            for label in ("确认发布", "确定", "继续发布", "我知道了"):
                btn = editor.get_by_role("button", name=label)
                if await btn.count():
                    await btn.first.click(timeout=8_000)
                    await asyncio.sleep(2)
            body = await editor.inner_text("body")
            if "已提交成功" in body or "提交成功" in body:
                return
            if "分类" in body and "请选择" in body:
                raise BilibiliArticleError(
                    "发布前需在「发布设置」中选择专栏分类（可在创作中心手动补一次）"
                )
            raise BilibiliArticleError(
                "浏览器点击发布后未看到成功提示（请检查分类/封面设置）"
            )
        finally:
            await browser.close()


def publish_article_via_browser(aid: int, cred: dict) -> None:
    asyncio.run(_publish_article_via_browser_async(aid, cred))


def publish_forum_pack(
    pack_dir: Path,
    *,
    root: Path | None = None,
    account: str | None = None,
    dry_run: bool = False,
) -> dict:
    pack_dir = Path(pack_dir).resolve()
    data = parse_forum_pack(pack_dir)
    cred = load_bilibili_credentials(root=root, account=account)

    if dry_run:
        return {
            "title": data["title"],
            "pack_dir": str(pack_dir),
            "account": cred["account"],
            "dry_run": True,
            "sections": len(data.get("sections") or []),
        }

    banner_path = _pick_banner(pack_dir)
    banner_url = upload_article_image(banner_path, cred)
    ctype = _article_content_type()
    update_aid = _reuse_article_aid(pack_dir, root=root)
    if ctype == 3:
        content, image_urls = forum_pack_to_opus_content(
            data, cred=cred, upload_images=True
        )
    else:
        content, image_urls = forum_pack_to_html(data, cred=cred, upload_images=True)
    summary = _summary_from_sections(data.get("sections") or [])
    aid = save_article_draft(
        title=data["title"],
        html=content,
        summary=summary,
        banner_url=banner_url,
        image_urls=image_urls,
        cred=cred,
        content_type=ctype,
        aid=update_aid,
    )
    published = False
    publish_note = ""
    if _article_auto_publish_enabled():
        try:
            publish_article_via_browser(aid, cred)
            published = True
            publish_note = "已通过创作中心浏览器提交发布"
        except BilibiliArticleError as exc:
            publish_note = f"浏览器发布失败: {exc}"

    return {
        "title": data["title"],
        "pack_dir": str(pack_dir),
        "account": cred["account"],
        "aid": aid,
        "url": article_editor_url(aid),
        "banner": banner_url,
        "images": image_urls,
        "draft_only": not published,
        "published": published,
        "publish_note": publish_note,
        "content_type": ctype,
        "content_chars": len(content),
    }
