# AI财知道

**每天一个 AI 财经为什么**。Cursor Cloud Agent 联网调研 + Claude Opus 深读改编，AiHubMix 生图，本地 TTS + ffmpeg 合成竖屏视频，自动发布国内多平台。

> **运行环境：Windows**（定时任务日更）。完整部署见 [docs/WINDOWS-DEPLOY.md](docs/WINDOWS-DEPLOY.md)。

```powershell
.\setup-windows.ps1 -UseWinget          # 一键安装
.\scripts\login-cn.ps1 douyin           # 各平台登录
.\make-and-publish.ps1                  # 制作+发布（工作日新闻 / 周末科普）
.\register-daily-publish.ps1 -At 08:00  # 注册计划任务
```

## 主入口

| 命令 | 说明 |
|------|------|
| `.\make-and-publish.ps1` | 工作日：新闻槽位；周末：科普选题（默认条数见环境变量） |
| `.\make-and-publish.ps1 1` | 只跑 1 条 |
| `.\make-and-publish.ps1 1 --no-publish` | 只生成不发布 |
| `.\register-daily-publish.ps1 -At 08:00` | 注册每日定时任务 |
| `.\scripts\login-cn.ps1 <platform>` | 登录：douyin / bilibili / xiaohongshu / shipinhao / eastmoney / xueqiu / zhihu |

## 内容策略

- **工作日**：固定新闻槽位（A股板块 → 国内财经 → AI → 世界财经；大盘概述默认关闭）
- **周末**：Opus 动态科普选题（基础 / 量化 / 估值），概念级去重；标题优先故事/后果钩子，避免「X是什么」教材腔
- 工作日封面标题优先「反常事件 + 可感知钩子」；短视频默认 **2–3 页 / 约 45–75 秒**，强化 3 秒冷开场
- 风格校验 + 合规红线（禁股票代码/荐股等，见 `research.py`）

## 子栏目（同一主账号下的频道）

主账号 `AI财知道` 下分栏目，用**不同主题色 + 角标后缀**做视觉区分，定义见 `src/categories.py`：

| 栏目 | key | 主题色 |
|------|-----|--------|
| A股 | `astock` | 暖红 |
| 港美股 | `hkus` | 蓝 |
| AI资讯 | `ai` | 紫 |
| 量化 | `quant` | 绿 |
| 基础 | `basic` | 青 |

## 视觉风格

- **白板手绘** 方格纸底，黑色钢笔线 + 黄/紫荧光笔点缀
- **9:16 竖版**：图片占顶部 78%，底部 22% 留给字幕
- 中文短语（`on_image_text`）作为手写注释画进图里

## 流程

`make-and-publish.ps1` → `src/make_publish.py`：选题写稿 → Opus 深读改编 → 生图 → TTS/ffmpeg 合成 → 各平台发布 → 归档。

中间产物在 `logs/zh/`（如 `last_script_*.json`、`article_history.json`）；成片在 `output/zh/`，成功后归档到 `archive/published/YYYYMMDD/zh/`。

## 模型分工

| 步骤 | 服务 | 模型 |
|------|------|------|
| 写稿 | Cursor Cloud Agent | `CURSOR_MODEL_ID`（默认 `grok-4.5`） |
| 评审 / 改编 | AiHubMix Chat | `AIHUBMIX_TEXT_MODEL` |
| 生图 | AiHubMix Images | `AIHUBMIX_IMAGE_MODEL` |
| TTS | 豆包声音复刻 / DashScope CosyVoice | `TTS_PROVIDER` |

## 关键模块

```
src/
  make_publish.py         # 中文一键制作发布编排
  weekend_edu_topics.py   # 周末科普选题与去重
  cursor_daily_topics.py  # 工作日新闻槽位与 Cursor 写稿
  research.py             # 深读 → 改编（含校验）
  theme_clusters.py       # 概念簇 / 科普概念去重
  batch_aivideo.py        # 选题历史
  publish_pipeline.py     # 生图→合成→发布→归档
  enrich_images.py / video_compose.py / tts_client.py
```

## 发布

主流程由 `publish_pipeline.py` 按 `.env` 开关自动发布（抖音 / 小红书 / 视频号 / B站 / 东财 / 雪球 / 知乎 / 公众号等）。登录统一用：

```powershell
.\scripts\login-cn.ps1 douyin
.\scripts\login-cn.ps1 douyin --check
```

渠道与开关说明见 [docs/WINDOWS-DEPLOY.md](docs/WINDOWS-DEPLOY.md)。

## 各平台账号简介（复制用）

统一定位：**AI财知道｜每天一个 AI 和财经的为什么，A股·美股·港股都聊，用大白话讲清。**

> 合规提示：均已加「不构成投资建议」。财经平台风控较严，简介中避免“荐股/收益/带单”等字眼。

**昵称统一**：`AI财知道`

**一句话签名（≤20字）**

```
每天一个 AI 和财经的为什么，大白话讲清。
```

**抖音（个人简介 ≤70 字）**

```
每天用大白话讲清一个 AI 和财经热点，A股·美股·港股都聊。看懂趋势，不追涨杀跌。内容仅为分享，不构成投资建议。
```

**小红书**

```
📈 AI财知道｜每天一个 AI 与财经的为什么

用白板手绘 + 大白话，讲清财报要点、行业脉络与宏观趋势
覆盖 A 股、美股、港股与中概，也聊 AI 如何影响金融与商业

✏️ 人工选题与编排，重信息梳理，轻情绪化表述
⚠️ 内容为知识分享与个人观点，不构成投资建议，请自行判断
```

小红书头像（图中勿含汉字）：`assets/ai_caizhidao_avatar_xhs.png`；抖音可用 `assets/ai_caizhidao_avatar.png`。

**视频号 / B站 / 雪球 / 东财** 简介可沿用上方定位句，自行裁到各平台字数上限。

## 常用环境变量

| 变量 | 说明 |
|------|------|
| `AIVIDEO_MAX_VIDEOS_PER_RUN` | 工作日默认条数 |
| `AIVIDEO_WEEKEND_MAX_VIDEOS` | 周末科普条数（默认 3） |
| `AIVIDEO_EDU_DEDUP_DAYS` | 科普概念去重窗口（默认近似永不重复） |
| `AIVIDEO_CURSOR_REUSE_AGENT` | 同次日更复用 Cloud Agent |
| `AIVIDEO_PUBLISH_*` | 各平台发布开关 |

完整列表见 `.env.example`。

TTS 默认豆包声音复刻：

```ini
TTS_PROVIDER=doubao
VOLCENGINE_TTS_RESOURCE_ID=seed-icl-2.0
VOLCENGINE_TTS_SPEAKER=S_6uN8A8f22
```

## 克隆个人音色（一次性）

```powershell
.\.venv\Scripts\python.exe src\voice_clone.py your-clip.mp4
# 输出 voice_id，写入 .env 的 DASHSCOPE_TTS_VOICE（若用百炼）
```
