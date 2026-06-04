"""微信公众号 · 图文发布（API 草稿 + 浏览器发表兜底）。"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from html import escape
from pathlib import Path

from eastmoney_publisher import _chrome_path, parse_forum_pack, sau_home
from forum_pack_format import format_headline_plain, is_table_line, split_body_blocks
from paths import ROOT


class WechatPublishError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "1" if default else "0")
    return raw.lower() in ("1", "true", "yes", "on")


def wechat_enabled() -> bool:
    return _env_bool("AIVIDEO_PUBLISH_WECHAT", False)


ACCOUNT_ENV = "WECHAT_ACCOUNT"
DRAFT_LIST_URL = (
    "https://mp.weixin.qq.com/cgi-bin/appmsg?"
    "begin=0&count=10&type=77&action=list_card&lang=zh_CN"
)
HOME_URL = "https://mp.weixin.qq.com/"


def _token_from_url(url: str) -> str:
    m = re.search(r"[?&]token=(\d+)", url or "")
    return m.group(1) if m else ""


def _draft_list_url(token: str) -> str:
    if not token:
        raise WechatPublishError("未获取到公众平台 token，请先 ./wechat-login.sh")
    return f"{DRAFT_LIST_URL}&token={token}"


def cookie_path(root: Path | None = None, account: str | None = None) -> Path:
    account = account or _env(ACCOUNT_ENV, "main")
    path = sau_home(root) / "cookies" / f"wechat_{account}.json"
    if not path.is_file():
        raise WechatPublishError(
            f"未找到 cookie: {path}\n请先运行: ./wechat-login.sh"
        )
    return path


def profile_dir(root: Path | None = None, account: str | None = None) -> Path:
    account = account or _env(ACCOUNT_ENV, "main")
    return sau_home(root) / "cookies" / "browser_profiles" / f"wechat_{account}"


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
        raise WechatPublishError(
            "未安装 patchright。请先运行: ./setup-sau.sh"
        ) from exc


def _browser_publish_enabled() -> bool:
    return _env("WECHAT_BROWSER_PUBLISH", "1").lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


async def _draft_list_ready(page) -> bool:
    url = (page.url or "").lower()
    if "login" in url or "registermidpage" in url:
        return False
    if await page.get_by_text("请重新登录").count():
        return False
    if await page.get_by_role("link", name="草稿箱").count():
        return True
    if await page.locator('a[href*="appmsg"]').count():
        return True
    body = await page.inner_text("body")
    return "草稿箱" in body and "登录" not in body[:200]


async def _open_draft_list(page) -> None:
    await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=90_000)
    await asyncio.sleep(1.5)
    if not await _draft_list_ready(page):
        raise WechatPublishError("未登录公众平台，请先 ./wechat-login.sh")
    token = _token_from_url(page.url)
    link = page.locator('a[href*="appmsg"][href*="type=77"]').first
    if await link.count():
        try:
            await link.click(timeout=20_000, force=True)
        except Exception:
            await page.goto(_draft_list_url(token), wait_until="domcontentloaded", timeout=90_000)
    else:
        await page.goto(_draft_list_url(token), wait_until="domcontentloaded", timeout=90_000)
    await asyncio.sleep(2)
    body = await page.inner_text("body")
    if "请重新登录" in body:
        raise WechatPublishError("草稿箱页面要求重新登录，请 ./wechat-login.sh 后重试")
    if "草稿箱" not in body and "草稿" not in body:
        raise WechatPublishError("未能打开草稿箱列表")


async def _dismiss_blocking_dialogs(page) -> None:
    for label in ("我知道了",):
        clicked = await page.evaluate(
            """(label) => {
              for (const el of document.querySelectorAll('button, a')) {
                if ((el.textContent || '').trim() === label) { el.click(); return true; }
              }
              return false;
            }""",
            label,
        )
        if clicked:
            await asyncio.sleep(0.4)


async def _fetch_draft_app_id(page, title: str) -> int:
    mp_token = _token_from_url(page.url)
    if not mp_token:
        raise WechatPublishError("未获取到公众平台 token")
    snippet = title.strip()[:24]
    data = await page.evaluate(
        """async (token) => {
          const url = `/cgi-bin/appmsg?begin=0&count=10&type=77&action=list&token=${token}&lang=zh_CN&f=json&ajax=1`;
          const resp = await fetch(url, { credentials: 'include' });
          return await resp.json();
        }""",
        mp_token,
    )
    for item in (data.get("app_msg_info") or {}).get("item") or []:
        if snippet and snippet in str(item.get("title") or ""):
            app_id = item.get("app_id")
            if app_id:
                return int(app_id)
    raise WechatPublishError(f"草稿箱中未找到标题含「{snippet}」的草稿")


def _editor_url(page, app_id: int) -> str:
    mp_token = _token_from_url(page.url)
    return (
        "https://mp.weixin.qq.com/cgi-bin/appmsg?"
        f"t=media/appmsg_edit&action=edit&type=77&appmsgid={app_id}"
        f"&isNew=0&token={mp_token}&lang=zh_CN"
    )


async def _js_click_button(page, label: str) -> bool:
    return bool(
        await page.evaluate(
            """(label) => {
              for (const el of document.querySelectorAll('button, a.weui-desktop-btn, .weui-desktop-link')) {
                const text = (el.textContent || '').trim();
                if (text === label) { el.click(); return true; }
              }
              return false;
            }""",
            label,
        )
    )


async def _confirm_publish_dialogs(page) -> None:
    """发表弹窗：逐层确认（未群发 / 原创校验 / 二次确认）。"""
    blocking: list[str] = []

    for _round in range(8):
        await asyncio.sleep(1.0)
        dialog_text = await page.evaluate(
            """() => {
              const parts = [];
              for (const el of document.querySelectorAll(
                '.weui-desktop-dialog, .new_mass_send_dialog, .double_check_dialog'
              )) {
                const t = (el.innerText || '').trim();
                if (t) parts.push(t);
              }
              return parts.join('\\n');
            }"""
        )
        if "公众号尚未实名" in dialog_text:
            blocking.append("公众号尚未实名，请先在公众平台完成实名认证")
        if "未设置头像和名称" in dialog_text:
            blocking.append("公众号头像/名称未完善，请先在公众平台设置")
        if "正在增加群发次数" in dialog_text:
            blocking.append("群发次数正在恢复，请约 5 分钟后再试")
        if blocking:
            break

        clicked = False
        for label in ("继续发表", "继续群发"):
            if await _js_click_button(page, label):
                clicked = True
                await asyncio.sleep(1.5)
                break
        if clicked:
            continue

        for sel in (
            ".new_mass_send_dialog .weui-desktop-btn_primary",
            ".double_check_dialog .weui-desktop-btn_primary",
            ".weui-desktop-dialog__ft .weui-desktop-btn_primary",
        ):
            btn = page.locator(sel).last
            if await btn.count():
                text = (await btn.inner_text()).strip()
                if text == "发表":
                    try:
                        await btn.click(timeout=5_000, force=True)
                        clicked = True
                        await asyncio.sleep(1.5)
                        break
                    except Exception:
                        pass
        if clicked:
            continue

        if "未开启群发通知" in dialog_text or "群发通知" in dialog_text:
            if await _js_click_button(page, "发表"):
                await asyncio.sleep(1.5)
                continue

        if not any(
            k in dialog_text
            for k in ("发表", "群发", "原创校验", "未开启群发")
        ):
            break

    if blocking:
        raise WechatPublishError("；".join(blocking))


async def _verify_published(page, title: str, *, app_id: int | None = None) -> bool:
    snippet = title.strip()[:24]
    mp_token = _token_from_url(page.url)
    if not mp_token:
        return False

    state = await page.evaluate(
        """async ({ token, snippet, appId }) => {
          const draftResp = await fetch(
            `/cgi-bin/appmsg?begin=0&count=20&type=77&action=list&token=${token}&lang=zh_CN&f=json&ajax=1`,
            { credentials: 'include' }
          );
          const draftData = await draftResp.json();
          const drafts = (draftData.app_msg_info && draftData.app_msg_info.item) || [];
          const draftMatch = drafts.some((it) => (it.title || '').includes(snippet));

          const pubResp = await fetch(
            `/cgi-bin/appmsgpublish?sub=list&begin=0&count=10&token=${token}&lang=zh_CN&f=json&ajax=1`,
            { credentials: 'include' }
          );
          const pubText = await pubResp.text();
          let pubMatch = false;
          try {
            const pubData = JSON.parse(pubText);
            const pageStr = pubData.publish_page || pubData.publish_page || '';
            pubMatch = pageStr.includes(snippet);
          } catch (_) {
            pubMatch = pubText.includes(snippet);
          }

          return {
            draftCount: drafts.length,
            draftMatch,
            pubMatch,
            appStillDraft: appId
              ? drafts.some((it) => Number(it.app_id) === Number(appId))
              : draftMatch,
          };
        }""",
        {"token": mp_token, "snippet": snippet, "appId": app_id},
    )

    # 草稿被消费且发表记录有同名条目
    if state.get("pubMatch") and not state.get("appStillDraft"):
        return True
    # 草稿箱里已无该标题
    if not state.get("draftMatch") and state.get("pubMatch"):
        return True
    return False


async def _publish_draft_via_editor(page, title: str) -> None:
    await _open_draft_list(page)
    await _dismiss_blocking_dialogs(page)
    app_id = await _fetch_draft_app_id(page, title)
    edit_url = _editor_url(page, app_id)
    await page.goto(
        edit_url,
        wait_until="domcontentloaded",
        timeout=90_000,
    )
    await asyncio.sleep(3)
    await _dismiss_blocking_dialogs(page)
    pub_btn = page.locator(
        'button:has-text("发表"), .weui-desktop-btn:has-text("发表")'
    ).first
    if not await pub_btn.count():
        raise WechatPublishError("编辑器内未找到「发表」按钮")
    await pub_btn.click(timeout=15_000, force=True)
    await asyncio.sleep(2)
    await _confirm_publish_dialogs(page)
    await asyncio.sleep(3)
    if await _verify_published(page, title, app_id=app_id):
        return
    body = await page.inner_text("body")
    ok_markers = ("发表成功", "已发表", "提交成功", "发布成功")
    if any(m in body for m in ok_markers):
        return
    raise WechatPublishError(
        "浏览器发表未完成（请到 mp.weixin.qq.com 发表记录确认）"
    )


async def _open_draft_by_title(page, title: str) -> None:
    """兼容旧调用：改为走编辑器发表流程。"""
    await _publish_draft_via_editor(page, title)


async def _click_publish_in_editor(page) -> None:
    """兼容旧调用：发表确认已在 _publish_draft_via_editor 内完成。"""
    return


async def _launch_wechat_context(p, *, headless: bool, account: str | None):
    account = account or _env(ACCOUNT_ENV, "main")
    profile = profile_dir(account=account)
    profile.mkdir(parents=True, exist_ok=True)
    launch: dict = {
        "headless": headless,
        "args": ["--disable-blink-features=AutomationControlled", "--lang=zh-CN"],
    }
    chrome = _chrome_path()
    if chrome:
        launch["executable_path"] = chrome
    else:
        launch["channel"] = "chrome"

    cookie: Path | None = None
    try:
        cookie = cookie_path(account=account)
    except WechatPublishError:
        cookie = None

    if profile.is_dir() and any(profile.iterdir()):
        context = await p.chromium.launch_persistent_context(
            str(profile),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 1000},
            **launch,
        )
        return context, None

    if cookie and cookie.is_file() and cookie.stat().st_size > 64:
        browser = await p.chromium.launch(**launch)
        context = await browser.new_context(
            storage_state=str(cookie),
            locale="zh-CN",
            viewport={"width": 1440, "height": 1000},
        )
        return context, browser

    raise WechatPublishError("未登录，请先 ./wechat-login.sh")


async def _publish_draft_via_browser_async(title: str, *, account: str | None = None) -> None:
    _ensure_patchright()
    from patchright.async_api import async_playwright

    headless = _env("WECHAT_BROWSER_HEADLESS", "1").lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    async with async_playwright() as p:
        context, browser = await _launch_wechat_context(
            p, headless=headless, account=account
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await _publish_draft_via_editor(page, title)
        finally:
            if browser is not None:
                await browser.close()
            else:
                await context.close()


def publish_draft_via_browser(title: str, *, account: str | None = None) -> None:
    asyncio.run(_publish_draft_via_browser_async(title, account=account))


def _credentials() -> tuple[str, str]:
    app_id = _env("WECHAT_APP_ID")
    app_secret = _env("WECHAT_APP_SECRET")
    if not app_id or not app_secret:
        raise WechatPublishError(
            "未配置 WECHAT_APP_ID / WECHAT_APP_SECRET（见 .env）"
        )
    return app_id, app_secret


def _token_cache_path() -> Path:
    return ROOT / "logs" / "wechat_access_token.json"


def _http_get(url: str, *, timeout: int = 60) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise WechatPublishError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise WechatPublishError(f"网络请求失败: {exc}") from exc


def _http_post_json(url: str, payload: dict, *, timeout: int = 120) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise WechatPublishError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise WechatPublishError(f"网络请求失败: {exc}") from exc


def _http_post_multipart(
    url: str,
    *,
    fields: dict[str, str],
    file_field: str,
    filename: str,
    file_bytes: bytes,
    mime: str,
    timeout: int = 120,
) -> dict:
    boundary = "----WechatFormBoundary7MA4YWxkTrZu0gW"
    lines: list[bytes] = []
    for key, val in fields.items():
        lines.append(f"--{boundary}\r\n".encode())
        lines.append(
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
        )
        lines.append(f"{val}\r\n".encode())
    lines.append(f"--{boundary}\r\n".encode())
    lines.append(
        f'Content-Disposition: form-data; name="{file_field}"; '
        f'filename="{filename}"\r\n'.encode()
    )
    lines.append(f"Content-Type: {mime}\r\n\r\n".encode())
    lines.append(file_bytes)
    lines.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(lines)
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise WechatPublishError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise WechatPublishError(f"网络请求失败: {exc}") from exc


def _check_wechat_resp(payload: dict, *, action: str) -> dict:
    errcode = payload.get("errcode", 0)
    if errcode not in (0, None):
        raise WechatPublishError(
            f"{action} 失败 errcode={errcode}: {payload.get('errmsg', payload)}"
        )
    return payload


def get_access_token(*, force_refresh: bool = False) -> str:
    app_id, app_secret = _credentials()
    cache = _token_cache_path()
    if not force_refresh and cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if (
                data.get("app_id") == app_id
                and data.get("access_token")
                and int(data.get("expires_at") or 0) > int(__import__("time").time()) + 120
            ):
                return str(data["access_token"])
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    url = (
        "https://api.weixin.qq.com/cgi-bin/token?"
        + urllib.parse.urlencode(
            {
                "grant_type": "client_credential",
                "appid": app_id,
                "secret": app_secret,
            }
        )
    )
    payload = _check_wechat_resp(_http_get(url), action="获取 access_token")
    token = str(payload.get("access_token") or "")
    if not token:
        raise WechatPublishError(f"未返回 access_token: {payload}")
    expires_in = int(payload.get("expires_in") or 7200)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps(
            {
                "app_id": app_id,
                "access_token": token,
                "expires_at": int(__import__("time").time()) + expires_in,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return token


def _inline_html(text: str) -> str:
    text = escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", text)
    return text


def _wechat_paragraph(text: str) -> str:
    return (
        '<p style="margin:16px 0;line-height:1.75;font-size:16px;'
        'color:#3f3f3f;text-align:justify;">'
        f"{_inline_html(text)}</p>"
    )


def _wechat_heading(text: str) -> str:
    """微信正文不支持 h1-h6，用加粗段落模拟小节标题。"""
    label = format_headline_plain(text)
    if "风险提示" in label:
        return (
            '<p style="margin:28px 0 12px;line-height:1.6;font-size:17px;'
            'font-weight:bold;color:#333;">'
            f"{escape(label)}</p>"
        )
    return (
        '<p style="margin:24px 0 12px;line-height:1.6;font-size:17px;'
        'font-weight:bold;color:#333;">'
        f"{escape(label)}</p>"
    )


def _wechat_caption(text: str) -> str:
    return (
        '<p style="margin:8px 0 20px;line-height:1.6;font-size:14px;'
        'color:#888;text-align:center;">'
        f"<em>{_inline_html(text)}</em></p>"
    )


def _wechat_disclaimer(text: str) -> str:
    return (
        '<p style="margin:12px 0;line-height:1.7;font-size:14px;'
        'color:#888;text-align:justify;">'
        f"<em>{_inline_html(text)}</em></p>"
    )


def _table_html(lines: list[str]) -> str:
    rows: list[list[str]] = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-+:?", c or "-") for c in cells):
            continue
        rows.append(cells)
    if not rows:
        return ""
    parts = [
        '<table style="border-collapse:collapse;width:100%;margin:16px 0;'
        'font-size:14px;line-height:1.6;">'
    ]
    for i, row in enumerate(rows):
        parts.append("<tr>")
        for cell in row:
            style = (
                "border:1px solid #e5e5e5;padding:8px 10px;vertical-align:top;"
            )
            if i == 0:
                style += "background:#f7f7f7;font-weight:bold;color:#333;"
            tag = "th" if i == 0 else "td"
            parts.append(f'<{tag} style="{style}">{_inline_html(cell)}</{tag}>')
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def _body_blocks_to_html(body: str) -> str:
    parts: list[str] = []
    for block in split_body_blocks(body):
        lines = block.splitlines()
        if lines and is_table_line(lines[0]):
            table = _table_html(lines)
            if table:
                parts.append(table)
            continue
        parts.append(_wechat_paragraph(block))
    return "\n".join(parts)


def forum_pack_to_html(data: dict, *, image_urls: dict[str, str]) -> str:
    parts: list[str] = []
    summary = _summary_from_data(data)
    if summary:
        parts.append(_wechat_paragraph(f"【摘要】{summary}"))
    for sec in data.get("sections") or []:
        headline = (sec.get("headline") or "").strip()
        if headline:
            parts.append(_wechat_heading(headline))
        body = (sec.get("body") or "").strip()
        if body:
            parts.append(_body_blocks_to_html(body))
        img = sec.get("image")
        if img:
            url = image_urls.get(str(img))
            if url:
                parts.append(
                    '<p style="margin:16px 0;text-align:center;">'
                    f'<img src="{escape(url)}" style="max-width:100%;height:auto;" />'
                    "</p>"
                )
        caption = (sec.get("caption") or "").strip()
        if caption:
            parts.append(_wechat_caption(caption))
    disclaimer = (data.get("disclaimer") or "").strip()
    if disclaimer:
        parts.append(_wechat_disclaimer(disclaimer))
    html = "\n".join(parts).strip()
    if not html:
        raise WechatPublishError("正文 HTML 为空")
    if len(html) > 20000:
        raise WechatPublishError(
            f"正文超过微信 2 万字符限制（当前约 {len(html)} 字符）"
        )
    return html


def _summary_from_data(data: dict, *, max_len: int = 120) -> str:
    for sec in data.get("sections") or []:
        headline = (sec.get("headline") or "").strip()
        if headline in ("摘要",):
            body = re.sub(r"\s+", " ", (sec.get("body") or "").strip())
            if body:
                return body[:max_len]
    title = str(data.get("title") or "")
    return title[:max_len]


def _thumb_jpeg_bytes(cover_path: Path) -> bytes:
    try:
        from PIL import Image
    except ImportError as exc:
        raise WechatPublishError("需要 Pillow 压缩封面（pip install Pillow）") from exc

    with Image.open(cover_path) as img:
        img = img.convert("RGB")
        img.thumbnail((900, 500))
        for quality in range(85, 35, -5):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            if len(data) <= 64000:
                return data
    raise WechatPublishError("封面压缩后仍超过 64KB，请换更小图片")


def upload_article_image(token: str, image_path: Path) -> str:
    suffix = image_path.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    url = f"https://api.weixin.qq.com/cgi-bin/media/uploadimg?access_token={token}"
    payload = _http_post_multipart(
        url,
        fields={},
        file_field="media",
        filename=image_path.name,
        file_bytes=image_path.read_bytes(),
        mime=mime,
    )
    payload = _check_wechat_resp(payload, action="上传正文图片")
    img_url = str(payload.get("url") or "")
    if not img_url:
        raise WechatPublishError(f"上传图片失败: {image_path}")
    return img_url


def upload_thumb_material(token: str, cover_path: Path) -> str:
    url = (
        "https://api.weixin.qq.com/cgi-bin/material/add_material?"
        f"access_token={token}&type=thumb"
    )
    payload = _http_post_multipart(
        url,
        fields={"description": json.dumps({"introduction": "", "author": ""})},
        file_field="media",
        filename="cover.jpg",
        file_bytes=_thumb_jpeg_bytes(cover_path),
        mime="image/jpeg",
    )
    payload = _check_wechat_resp(payload, action="上传封面")
    media_id = str(payload.get("media_id") or "")
    if not media_id:
        raise WechatPublishError("上传封面未返回 media_id")
    return media_id


def _plain_text_from_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", "", html or "")
    return re.sub(r"\s+", " ", text).strip()


def _content_starts_with_title(html: str, title: str) -> bool:
    title = title.strip()
    if not title:
        return False
    plain = _plain_text_from_html(html)
    return plain.startswith(title)


def _list_draft_items(token: str, *, with_content: bool) -> list[dict]:
    batch = _check_wechat_resp(
        _http_post_json(
            f"https://api.weixin.qq.com/cgi-bin/draft/batchget?access_token={token}",
            {"offset": 0, "count": 20, "no_content": 0 if with_content else 1},
        ),
        action="获取草稿列表",
    )
    return list(batch.get("item") or [])


def _delete_draft(token: str, media_id: str) -> None:
    _check_wechat_resp(
        _http_post_json(
            f"https://api.weixin.qq.com/cgi-bin/draft/delete?access_token={token}",
            {"media_id": media_id},
        ),
        action="删除草稿",
    )


def delete_drafts_by_title(title: str, token: str | None = None) -> int:
    """删除标题相同或标题混入正文的损坏草稿，避免草稿箱里留下错误版本。"""
    token = token or get_access_token()
    title = title.strip()
    deleted = 0
    for item in _list_draft_items(token, with_content=True):
        media_id = str(item.get("media_id") or "")
        if not media_id:
            continue
        article = ((item.get("content") or {}).get("news_item") or [{}])[0]
        draft_title = str(article.get("title") or "").strip()
        content = str(article.get("content") or "")
        broken = (
            not draft_title
            and title
            and _content_starts_with_title(content, title)
        )
        if draft_title == title or broken:
            _delete_draft(token, media_id)
            deleted += 1
    return deleted


def clear_all_drafts(token: str | None = None) -> int:
    """删除草稿箱全部草稿，返回删除数量。"""
    token = token or get_access_token()
    deleted = 0
    while True:
        items = _list_draft_items(token, with_content=False)
        if not items:
            break
        for item in items:
            media_id = str(item.get("media_id") or "")
            if not media_id:
                continue
            _delete_draft(token, media_id)
            deleted += 1
    return deleted


def get_draft_article(token: str, media_id: str) -> dict:
    payload = _check_wechat_resp(
        _http_post_json(
            f"https://api.weixin.qq.com/cgi-bin/draft/get?access_token={token}",
            {"media_id": media_id},
        ),
        action="获取草稿",
    )
    items = payload.get("news_item") or []
    if not items:
        raise WechatPublishError(f"草稿不存在: {media_id}")
    return items[0]


def _validate_draft_article(article: dict, *, title: str, html: str) -> None:
    draft_title = str(article.get("title") or "").strip()
    content = str(article.get("content") or "")
    if draft_title != title.strip():
        raise WechatPublishError(
            f"草稿标题校验失败: {draft_title!r} != {title.strip()!r}"
        )
    if _content_starts_with_title(content, title):
        raise WechatPublishError("草稿正文开头误含标题，请重试或清空草稿箱")
    if "【摘要】" not in _plain_text_from_html(content)[:80]:
        raise WechatPublishError("草稿正文未从【摘要】开始，格式异常")
    if content.strip() != html.strip():
        # 微信可能微调 HTML，只校验正文开头结构
        if not content.lstrip().startswith("<p"):
            raise WechatPublishError("草稿正文 HTML 结构异常")


def add_draft(
    token: str,
    *,
    title: str,
    html: str,
    thumb_media_id: str,
    digest: str,
    author: str = "",
) -> str:
    article = {
        "article_type": "news",
        "title": title[:32],
        "author": (author or _env("WECHAT_AUTHOR", "AI财知道"))[:16],
        "digest": digest[:120],
        "content": html,
        "thumb_media_id": thumb_media_id,
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
    }
    url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}"
    payload = _check_wechat_resp(
        _http_post_json(url, {"articles": [article]}),
        action="新增草稿",
    )
    media_id = str((payload.get("media_id") or ""))
    if not media_id:
        raise WechatPublishError(f"草稿未返回 media_id: {payload}")
    return media_id


def submit_publish(token: str, media_id: str) -> str:
    url = f"https://api.weixin.qq.com/cgi-bin/freepublish/submit?access_token={token}"
    payload = _check_wechat_resp(
        _http_post_json(url, {"media_id": media_id}),
        action="提交发布",
    )
    publish_id = str(payload.get("publish_id") or "")
    if not publish_id:
        raise WechatPublishError(f"发布未返回 publish_id: {payload}")
    return publish_id


def get_publish_status(token: str, publish_id: str) -> dict:
    url = f"https://api.weixin.qq.com/cgi-bin/freepublish/get?access_token={token}"
    return _check_wechat_resp(
        _http_post_json(url, {"publish_id": publish_id}),
        action="查询发布状态",
    )


def publish_forum_pack(
    pack_dir: Path,
    *,
    draft_only: bool | None = None,
    dry_run: bool = False,
) -> dict:
    pack_dir = Path(pack_dir).resolve()
    data = parse_forum_pack(pack_dir)
    if dry_run:
        return {
            "title": data["title"],
            "pack_dir": str(pack_dir),
            "sections": len(data.get("sections") or []),
            "dry_run": True,
        }

    if draft_only is None:
        draft_only = _env_bool("WECHAT_DRAFT_ONLY", True)

    token = get_access_token()
    image_urls: dict[str, str] = {}
    for sec in data.get("sections") or []:
        img = sec.get("image")
        if img and str(img) not in image_urls:
            image_urls[str(img)] = upload_article_image(token, Path(img))

    html = forum_pack_to_html(data, image_urls=image_urls)
    if _content_starts_with_title(html, data["title"]):
        raise WechatPublishError("正文 HTML 不应包含文章标题")
    thumb_media_id = upload_thumb_material(token, Path(data["cover"]))
    digest = _summary_from_data(data)
    removed = delete_drafts_by_title(data["title"], token=token)
    if removed:
        print(f"  已清理旧草稿 {removed} 篇", flush=True)
    draft_media_id = add_draft(
        token,
        title=data["title"],
        html=html,
        thumb_media_id=thumb_media_id,
        digest=digest,
    )
    saved = get_draft_article(token, draft_media_id)
    _validate_draft_article(saved, title=data["title"], html=html)

    published = False
    publish_id = ""
    publish_status: dict | None = None
    publish_note = ""
    if not draft_only:
        try:
            publish_id = submit_publish(token, draft_media_id)
            import time

            for _ in range(12):
                time.sleep(2)
                publish_status = get_publish_status(token, publish_id)
                status = int(publish_status.get("publish_status") or 0)
                # 0=成功 1=发布中 2=原创失败 3=常规失败 4=平台审核不通过 5=用户删除 6=系统封禁
                if status == 0:
                    published = True
                    publish_note = "API 提交发布成功"
                    break
                if status not in (0, 1):
                    raise WechatPublishError(
                        f"发布失败 publish_status={status}: {publish_status}"
                    )
        except WechatPublishError as exc:
            if "48001" in str(exc) or "api unauthorized" in str(exc).lower():
                publish_note = "API 自动发布不可用（个人未认证订阅号无 freepublish 权限）"
            else:
                raise

    if not published and not draft_only and _browser_publish_enabled():
        try:
            publish_draft_via_browser(data["title"])
            published = True
            if publish_note:
                publish_note = f"{publish_note}；已通过创作中心浏览器提交发表"
            else:
                publish_note = "已通过创作中心浏览器提交发表"
        except WechatPublishError as browser_exc:
            suffix = f"浏览器发表失败: {browser_exc}"
            publish_note = f"{publish_note}；{suffix}" if publish_note else suffix

    return {
        "title": data["title"],
        "pack_dir": str(pack_dir),
        "draft_media_id": draft_media_id,
        "publish_id": publish_id,
        "published": published,
        "draft_only": not published,
        "digest": digest,
        "images": list(image_urls.values()),
        "publish_status": publish_status,
        "publish_note": publish_note,
    }
