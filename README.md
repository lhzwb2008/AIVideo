# AIVideo

**每日一个 AI 热点** → Cursor 调研 → AiHubMix 生图 → 本地 TTS+ffmpeg 合成竖屏视频 →（手动）发布抖音。

```bash
./run-aivideo.sh
./publish-all-douyin.sh    # 制作完成后单独执行
```

## 内容策略

1. Cursor 联网搜索，**锁定当天 1 个大众向 AI 热点**
2. 基于 1 篇权威报道提取事实，**口语化口播**（比喻、调侃 OK，不编造数字）
3. 5 页结构：cover → insight → data → story → outro
4. 每页输出 `chapter_title` / `concept` / `narration` / `image_prompt` / `on_image_text`
5. `title` 字面必须含 `keyword`；脚本过结构 + 风格校验，失败自动让 Agent 修正一轮

## 视觉风格

- **白板手绘** 方格纸底，黑色钢笔线 + 黄/紫荧光笔点缀
- **9:16 竖版**：图片占顶部 78%，底部 22% 留给字幕 + 进度条
- 中文短语（`on_image_text`）作为手写注释直接画进图里
- 章节角标 + `01/05` 页码 + 分段进度点

## 流程

| 步骤 | 命令 | 产出 |
|------|------|------|
| 一键制作 | `./run-aivideo.sh "话题"` | 调研 + 生图 + 合成 → `output/*.mp4` |
| 批量制作 | `./run-batch-aivideo.sh` | 多条视频 |
| 仅生图 | `./run-enrich-images.sh logs/last_script.json` | 写入 `slide.image_path` |
| 仅合成 | `./run-compose.sh logs/last_script.json` | TTS + ffmpeg → `output/*.mp4` |
| **发布** | `./publish-all-douyin.sh` | 发布 `output/` 下未发布的 MP4 |

## 关键模块

```
src/
  research.py        # Cursor Agent 调研 + 风格校验
  image_client.py    # AiHubMix gpt-image-2 生图（含重试）
  enrich_images.py   # 逐页生图，断点续跑
  tts_client.py      # 百炼 CosyVoice TTS
  voice_clone.py     # 百炼音色克隆（一次性）
  video_compose.py   # PIL 渲染进度条/章节，ffmpeg 合成 + 字幕
  batch_aivideo.py   # 批量编排
```

## 抖音发布（与制作分离）

抖音无开放 API，需 Playwright + 扫码 cookie。

```bash
./setup-sau.sh                       # 一次性
./douyin-login.sh                    # 扫码登录
./publish-all-douyin.sh              # 发布全部未发布的 mp4
./publish-all-douyin.sh --dry-run    # 预览
./publish-all-douyin.sh --assist     # 半自动
./publish-douyin.sh output/xxx.mp4   # 单条
```

记录：`logs/published_videos.json`、`logs/video_manifest.jsonl`。

## 环境变量

见 `.env.example`：`CURSOR_*`、`AIHUBMIX_*`、`DASHSCOPE_*`、`AIVIDEO_*`、`SAU_*`、`DOUYIN_*`。

## 克隆个人音色（一次性）

把一段你自己的音频/视频（≥15s 清晰人声）放到项目根，然后：

```bash
python3 src/voice_clone.py test.mp4
# 输出 voice_id，复制到 .env 的 DASHSCOPE_TTS_VOICE
```
