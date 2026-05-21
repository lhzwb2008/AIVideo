#!/usr/bin/env python3
"""AI 新闻幻灯片视频：脚本 → 分镜出图 → TTS → FFmpeg 合成（竖屏 9:16）"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
import urllib.request
from datetime import datetime
from pathlib import Path

import edge_tts
import requests
from PIL import Image, ImageDraw, ImageFont
from dashscope import ImageSynthesis

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "slideshow"
(ROOT / "output").mkdir(parents=True, exist_ok=True)
(ROOT / "logs").mkdir(parents=True, exist_ok=True)
W, H = 1080, 1920
SLIDE_COUNT = 5


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        sys.exit("缺少 .env")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"'))


def fallback_plan(topic: str) -> dict:
    return {
        "title": topic[:15],
        "slides": [
            {
                "headline": "今日 AI 要闻",
                "bullets": ["Agent 工具链持续升温", "多模态模型迭代加速", "视频生成进入实用阶段"],
                "narration": f"欢迎收看本期 AI 六十秒，今天聚焦{topic}，我们先看三条值得跟进的动态。",
                "image_prompt": "futuristic AI news studio, blue gradient, vertical, no text",
            },
            {
                "headline": "Agent 与自动化",
                "bullets": ["工作流编排成为标配", "插件生态快速扩张", "人机协同而非替代"],
                "narration": "第一条，Agent 正在从对话走向可执行流水线，企业和个人都在把重复任务交给智能体。",
                "image_prompt": "abstract network nodes automation, dark blue, vertical, no text",
            },
            {
                "headline": "大模型能力边界",
                "bullets": ["推理成本继续下降", "长上下文更常见", "垂直场景微调增多"],
                "narration": "第二条，大模型侧重点是更稳的推理和更便宜的调用，行业应用开始比拼落地而不是参数。",
                "image_prompt": "large language model visualization chips, cinematic, vertical, no text",
            },
            {
                "headline": "视频生成范式",
                "bullets": ["先出图再动效更可控", "幻灯片式资讯更易量产", "竖屏短视频仍是主战场"],
                "narration": "第三条，视频生成从一键文生视频，转向分镜出图再合成，资讯类内容更像专业幻灯片。",
                "image_prompt": "video editing timeline storyboard, professional, vertical, no text",
            },
            {
                "headline": "本期小结",
                "bullets": ["关注可复制的生产流程", "先质量后全自动发布", "下期见"],
                "narration": "以上就是今天的 AI 六十秒，点赞关注，我们下期继续带你速览前沿。",
                "image_prompt": "minimal tech outro background lights, vertical, no text",
            },
        ],
    }


def llm_slides(topic: str) -> dict:
    base = os.environ["DASHSCOPE_BASE_URL"].rstrip("/")
    key = os.environ["DASHSCOPE_API_KEY"]
    model = os.environ.get("DASHSCOPE_MODEL", "qwen-plus")
    prompt = f"""你是 AI 资讯编导。主题：{topic}

请输出严格 JSON（不要 markdown），结构：
{{
  "title": "本期标题15字内",
  "slides": [
    {{
      "headline": "本页大标题",
      "bullets": ["要点1", "要点2", "要点3"],
      "narration": "本页口播约40-50字",
      "image_prompt": "英文，科技新闻幻灯片背景，无文字，竖构图，简洁专业，与要点相关"
    }}
  ]
}}
要求：slides 数组恰好 {SLIDE_COUNT} 页；第1页开场，最后一页总结；内容真实克制，避免编造具体数字。"""
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
        },
        timeout=120,
    )
    if resp.status_code != 200:
        print(f"  LLM 不可用 ({resp.status_code})，使用内置分镜模板:", resp.text[:120])
        return fallback_plan(topic)
    text = resp.json()["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError(f"LLM 未返回 JSON: {text[:500]}")
    return json.loads(m.group())


def gen_placeholder(path: Path, hue: int) -> None:
    img = Image.new("RGB", (W, H), (10 + hue, 25 + hue, 55 + hue * 2))
    d = ImageDraw.Draw(img)
    for y in range(0, H, 80):
        d.line([(0, y), (W, y)], fill=(20 + hue, 40 + hue, 90 + hue))
    img.save(path)


def gen_image(prompt: str, path: Path) -> None:
    api_key = os.environ["DASHSCOPE_API_KEY"]
    try:
        rsp = ImageSynthesis.call(
            api_key=api_key,
            model=os.environ.get("WANX_MODEL", "wanx2.1-t2i-turbo"),
            prompt=prompt,
            n=1,
            size="720*1280",
        )
        if rsp.status_code == 200:
            url = rsp.output.results[0].url
            urllib.request.urlretrieve(url, path)
            return
        print(f"  出图 API: {rsp.code} {rsp.message}")
    except Exception as e:
        print(f"  出图异常: {e}")
    gen_placeholder(path, hue=hash(prompt) % 40)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size=size, index=1 if bold and p.endswith(".ttc") else 0)
    return ImageFont.load_default()


def render_slide(
    bg_path: Path,
    headline: str,
    bullets: list[str],
    idx: int,
    title: str,
    out_path: Path,
) -> None:
    bg = Image.open(bg_path).convert("RGB").resize((W, H), Image.LANCZOS)
    overlay = Image.new("RGBA", (W, H), (8, 18, 42, 200))
    canvas = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(canvas)

    draw.rectangle((0, 0, W, 140), fill=(12, 28, 64))
    draw.text((48, 42), f"AI 60s · {title}", fill=(120, 180, 255), font=font(36))
    draw.text((48, 200), headline, fill=(255, 255, 255), font=font(64, bold=True))

    y = 520
    for i, b in enumerate(bullets[:4]):
        draw.rounded_rectangle((48, y, W - 48, y + 100), radius=16, fill=(20, 40, 80))
        draw.text((72, y + 24), f"• {b}", fill=(230, 240, 255), font=font(40))
        y += 130

    draw.text((48, H - 80), f"{idx + 1}/{SLIDE_COUNT}", fill=(150, 170, 200), font=font(32))
    canvas.save(out_path, quality=95)


async def tts_edge(text: str, mp3: Path) -> None:
    voice = os.environ.get("TTS_VOICE", "zh-CN-YunxiNeural")
    comm = edge_tts.Communicate(text, voice)
    await comm.save(str(mp3))


def tts_macos(text: str, mp3: Path) -> None:
    aiff = mp3.with_suffix(".aiff")
    voice = os.environ.get("TTS_VOICE", "Ting-Ting")
    subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
    run_ffmpeg(["-i", str(aiff), "-c:a", "libmp3lame", "-q:a", "4", str(mp3)])
    aiff.unlink(missing_ok=True)


async def tts(text: str, mp3: Path) -> None:
    try:
        await tts_edge(text, mp3)
        if mp3.stat().st_size > 500:
            return
    except Exception as e:
        print(f"  edge-tts 失败，改用 macOS say: {e}")
    tts_macos(text, mp3)


def run_ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", *args], check=True, capture_output=True)


def slide_clip(png: Path, mp3: Path, clip: Path) -> None:
    run_ffmpeg(
        [
            "-loop",
            "1",
            "-i",
            str(png),
            "-i",
            str(mp3),
            "-c:v",
            "libx264",
            "-tune",
            "stillimage",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-vf",
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
            str(clip),
        ]
    )


def concat_clips(clips: list[Path], out_mp4: Path) -> None:
    lst = OUT / "concat.txt"
    lst.write_text("\n".join(f"file '{c.resolve()}'" for c in clips), encoding="utf-8")
    run_ffmpeg(
        ["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out_mp4)]
    )


def main() -> None:
    import asyncio

    load_env()
    topic = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("COZE_WORKFLOW_TOPIC", "今日AI新闻")
    OUT.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    work = OUT / ts
    work.mkdir(parents=True, exist_ok=True)

    print("[1/4] 生成分镜脚本 …")
    plan = llm_slides(topic)
    (work / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    slides = plan["slides"][:SLIDE_COUNT]
    title = plan.get("title", topic)

    clips: list[Path] = []
    for i, s in enumerate(slides):
        print(f"[2/4] 第 {i + 1} 页：出图 …")
        bg = work / f"bg_{i}.png"
        gen_image(s["image_prompt"], bg)

        print(f"[3/4] 第 {i + 1} 页：排版 + 配音 …")
        frame = work / f"slide_{i}.png"
        render_slide(bg, s["headline"], s.get("bullets", []), i, title, frame)
        mp3 = work / f"narration_{i}.mp3"
        asyncio.run(tts(s["narration"], mp3))
        clip = work / f"clip_{i}.mp4"
        slide_clip(frame, mp3, clip)
        clips.append(clip)

    final = ROOT / "output" / f"slideshow_{ts}.mp4"
    print("[4/4] 合成成片 …")
    concat_clips(clips, final)
    print("完成:", final)
    print("分镜目录:", work)


if __name__ == "__main__":
    main()
