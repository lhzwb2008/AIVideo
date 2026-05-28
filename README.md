# AI财知道

**每天一个 AI 财经为什么**。从 AI、财经、美股和中概股热点里挑最值得解释的一件事，改编成问句标题的中文短视频。Cursor Cloud Agent 联网搜索 + 深读，Claude Opus 4.7 评审与改编，AiHubMix 生图，本地 TTS + ffmpeg 合成竖屏视频，（手动）发布抖音。

```bash
./make-and-publish.sh    # 生成 1 条并自动发布，成功后归档
./schedule.sh            # 安装/重启每日定时任务
```

## 内容策略

1. **找文章**（Exa / 固定信息源）：按 AI、财经、美股和中概股真实热度（HN/X/媒体/Reddit/newsletter/知乎/微博/即刻/公众号 10w+）拉**中英文各 3 篇**热点长文
2. **挑最佳**（Claude Opus 4.7 / low thinking）：自动评审 6 篇候选，输出选中理由 + 候选排名（批量模式下逐次挑 1 篇并 exclude 已选）
3. **深读原文**（Cursor Cloud Agent）：再次打开文章，抽出段落 outline / 所有数字 / 所有引语 / 人物 / 场景 / 真实开头结尾 / 作者立场
4. **改编脚本**（Claude Opus 4.7 / low thinking）：基于深读细节按文章自身节奏拆 3-10 页中文问答短视频脚本，标题统一为「什么是/为什么/意味着什么/财报好不好」这类搜索友好问句，**不套模板，不强加 5 页**
5. 风格校验：禁用词、客观引述次数、cover 钩子、narration 字数等；失败自动让 Opus 修正

## 视觉风格

- **白板手绘** 方格纸底，黑色钢笔线 + 黄/紫荧光笔点缀
- **9:16 竖版**：图片占顶部 78%，底部 22% 留给字幕
- 中文短语（`on_image_text`）作为手写注释画进图里
- 章节角标 + 页码

## 流程

| 步骤 | 命令 | 产出 |
|------|------|------|
| Make + publish | `./make-and-publish.sh` | Exa 找选题 + 深读 + 改编 + 生图 + 合成 + 发布抖音 + 归档 |
| Schedule | `./schedule.sh` | 安装/重启每日定时任务，自动制作发布并归档 |
| Debug image only | `./scripts/run-enrich-images.sh logs/last_script.json` | 写入 `slide.image_path` |
| Debug compose only | `./scripts/run-compose.sh logs/last_script.json` | TTS + ffmpeg → `output/*.mp4` |
| Debug publish only | `./scripts/publish-douyin.sh output/xxx.mp4 --script logs/xxx.json` | 调试用，正常不用手动执行 |

调研中间产物（都在 `logs/`）：

| 文件 | 内容 |
|------|------|
| `last_article_candidates.json` | 3 篇候选文章 |
| `last_article_decision.json` | Opus 选 1 篇的理由与排名 |
| `last_article.json` | 选定的那篇 metadata |
| `last_article_details.json` | Cursor 深读出来的段落/数字/引语/场景 |
| `last_script.json` | 最终脚本（被生图与合成消费） |

跳过重复步骤（调试用）：
- `AIVIDEO_USE_SELECTION=1` — 跳过找文章 + 深读，复用 `last_article.json` / `last_article_details.json`，只重跑改编与下游
- `AIVIDEO_MANUAL_PICK=1` — 关掉 Opus 自动选，让你手动挑

## 模型分工

| 步骤 | 服务 | 模型 |
|------|------|------|
| 找文章 / 深读 | Cursor Cloud Agent | `CURSOR_MODEL_ID`（默认 `composer-2.5`） |
| 评审 / 改编 | AiHubMix Chat | `AIHUBMIX_TEXT_MODEL`（默认 `claude-opus-4-7`，`reasoning_effort=low`） |
| 生图 | AiHubMix Images | `AIHUBMIX_IMAGE_MODEL`（默认 `gpt-image-2`） |
| TTS | 豆包声音复刻 / DashScope CosyVoice | `TTS_PROVIDER` |

## 关键模块

```
src/
  research.py        # 唯一管线：找文章 → 评审 → 深读 → 改编（含校验+修正轮）
  text_client.py     # AiHubMix chat（thinking 预算最低）
  image_client.py    # AiHubMix gpt-image-2 生图（含重试）
  enrich_images.py   # 逐页生图，断点续跑
  tts_client.py      # TTS 分流：豆包声音复刻 / 百炼 CosyVoice
  voice_clone.py     # 百炼音色克隆（一次性）
  video_compose.py   # PIL 底图 + ffmpeg 合成 + 字幕
  cursor_client.py   # Cursor Cloud Agents REST + SSE
  batch_aivideo.py   # 批量编排（按 URL 去重）
  douyin_*.py / sau_client.py / publish_*.py  # 抖音发布（vendor/social-auto-upload）
```

## 抖音发布（与制作分离）

抖音无开放 API，需 Playwright + 扫码 cookie。

```bash
./setup-sau.sh                       # 一次性
./douyin-login.sh                    # 扫码登录
./make-and-publish.sh                # 正常入口：制作完成后自动发布
./scripts/publish-douyin.sh output/xxx.mp4   # 单条调试
```

记录：`logs/published_videos.json`、`logs/video_manifest.jsonl`。

## 每日定时（macOS launchd）

每天定时跑一遍「搜近 24h 中英文 AI/财经热点 → 制作 2 条 → 发布 → 归档」：

```bash
./schedule.sh            # 安装/重启守护（改完代码也是这条）
./schedule.sh --now      # 重启并立刻试跑一次
./schedule.sh --status   # 看当前调度
./schedule.sh --stop     # 卸载守护

tail -f logs/schedule_stdout.log
```

`.env` 配置（缺省即取默认值）：

| 变量 | 默认 | 说明 |
|------|------|------|
| `DAILY_RUN_HOUR` | `10` | 每天几点跑（0-23） |
| `DAILY_RUN_MINUTE` | `0` | 几分 |
| `DAILY_RUN_COUNT` | `1` | 单次执行生成几条视频（仅在跑定时任务时生效；手动跑用 `./make-and-publish.sh [N]` 或 `AIVIDEO_MAX_VIDEOS_PER_RUN` 覆盖，手动入口默认 3） |
| `DAILY_RUN_DAYS` | `1` | 搜索时间窗（天） |

发布成功的视频会被移到 `archive/published/YYYYMMDD/`，第二天 `output/` 又是干净的等待新一批。

## 环境变量

见 `.env.example`：`CURSOR_*`、`AIHUBMIX_*`（含 `AIHUBMIX_TEXT_MODEL`、`AIHUBMIX_REASONING_EFFORT`、`AIHUBMIX_THINKING_BUDGET`）、`TTS_PROVIDER`、`VOLCENGINE_TTS_*`、`DASHSCOPE_*`、`AIVIDEO_*`、`SAU_*`、`DOUYIN_*`。

TTS 默认使用豆包声音复刻：

```bash
TTS_PROVIDER=doubao
VOLCENGINE_TTS_RESOURCE_ID=seed-icl-2.0
VOLCENGINE_TTS_SPEAKER=S_6uN8A8f22
```

如需切回百炼：

```bash
TTS_PROVIDER=dashscope
```

## 克隆个人音色（一次性）

把一段你自己的音频/视频（≥15s 清晰人声）放到项目根，然后：

```bash
python3 src/voice_clone.py your-clip.mp4
# 输出 voice_id，复制到 .env 的 DASHSCOPE_TTS_VOICE
```
