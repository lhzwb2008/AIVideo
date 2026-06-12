# AI财知道

**每天一个 AI 财经为什么**。Cursor Cloud Agent 联网调研 + Claude Opus 深读改编，AiHubMix 生图，本地 TTS + ffmpeg 合成竖屏视频，自动发布抖音并联动 YouTube / 小红书（可选），成功后归档。

```bash
./make-and-publish.sh    # 中文：五槽位 Cursor 写稿 → 改编 → 发布（默认 5 条）
./make-us-publish.sh     # 英文 US Market：三槽位 → YouTube/TikTok/IG/FB/LinkedIn
```

## 两条主流水线

| 流水线 | 入口 | 说明 |
|--------|------|------|
| 中文 | `./make-and-publish.sh [N]` | 固定五槽位：A股大盘 → A股板块 → 国内财经 → AI → 世界财经 |
| 英文 US | `./make-us-publish.sh [N]` | 三槽位 Cursor 写稿，发布 YouTube/TikTok 等 |

```bash
./make-and-publish.sh              # 默认 5 条（五槽位各 1）
./make-and-publish.sh 1              # 只跑 1 条
./make-and-publish.sh --slot astock_market   # 重跑指定槽位
./make-and-publish.sh --no-publish   # 只生成不发布

./make-us-publish.sh                 # 默认 3 条
./make-us-publish.sh --topic "Why did the Fed pause rate cuts?"
```

## 内容策略（中文）

每日按固定槽位顺序，Cursor Cloud Agent 联网写稿，Opus 深读改编为 3–4 页竖屏脚本；风格校验 + 合规红线（禁股票代码/荐股等，见 `research.py`）。

## 子栏目（同一主账号下的频道）

主账号 `AI财知道` 下分 5 个子栏目，只用**不同主题色 + 角标后缀**做视觉区分（角标显示成「AI财知道 · A股」等），定义见 `src/categories.py`：

| 栏目 | key | 主题色 | 如何归类 |
|------|-----|--------|----------|
| A股 | `astock` | 暖红 | 自动：命中涨停/科创板/沪深等 A股 信号 |
| 港美股 | `hkus` | 蓝 | 自动：命中美股/港股/中概/英伟达等信号 |
| AI资讯 | `ai` | 紫 | 自动：命中大模型/GPT/Claude/智能体等且与具体行情无关 |
| 量化 | `quant` | 绿 | 脚本 `category` 字段指定 |
| 基础 | `basic` | 青 | 自动（兜底）：讲通用财经概念/原理 |

- 合成时按脚本内容自动判定栏目（A股 > 港美股 > AI资讯 > 基础），判不出则用默认黄色主题。
- 栏目会写进脚本的 `category` 字段，被 `video_compose` 读出用于徽标/封面/尾页配色。

## 视觉风格

- **白板手绘** 方格纸底，黑色钢笔线 + 黄/紫荧光笔点缀
- **9:16 竖版**：图片占顶部 78%，底部 22% 留给字幕
- 中文短语（`on_image_text`）作为手写注释画进图里
- 章节角标 + 页码

## 流程

| 步骤 | 命令 | 产出 |
|------|------|------|
| 中文制作+发布 | `./make-and-publish.sh` | Cursor 写稿 + Opus 改编 + 生图 + 合成 + 发布 + 归档 |
| 英文制作+发布 | `./make-us-publish.sh` | Cursor 写稿 + 改编 + 生图 + 合成 + YT/TikTok 等 + 归档 |
| Debug image only | `./scripts/run-enrich-images.sh logs/last_script.json` | 写入 `slide.image_path` |
| Debug compose only | `./scripts/run-compose.sh logs/last_script.json` | TTS + ffmpeg → `output/*.mp4` |
| Debug YouTube | `./scripts/publish-youtube.sh output/xxx.mp4 --script logs/xxx.json` | 单条 YouTube 调试 |

调研中间产物（都在 `logs/`）：

| 文件 | 内容 |
|------|------|
| `last_article.json` | 选定的那篇 metadata |
| `last_article_details.json` | Cursor 深读出来的段落/数字/引语/场景 |
| `last_script.json` | 最终脚本（被生图与合成消费） |

跳过重复步骤（调试用）：
- `AIVIDEO_USE_SELECTION=1` — 跳过找文章 + 深读，复用 `last_article.json` / `last_article_details.json`，只重跑改编与下游

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
  research.py        # 核心管线：深读 → 改编（含校验+修正轮）
  cursor_daily_topics.py  # 中文五槽位选题与 Cursor 写稿
  us_cursor_topics.py     # 英文三槽位选题与 Cursor 写稿
  make_publish.py    # 中文一键制作发布编排
  make_us_publish.py # 英文 US Market 编排
  text_client.py     # AiHubMix chat（thinking 预算最低）
  image_client.py    # AiHubMix gpt-image-2 生图（含重试）
  enrich_images.py   # 逐页生图，断点续跑
  tts_client.py      # TTS 分流：豆包声音复刻 / 百炼 CosyVoice
  voice_clone.py     # 百炼音色克隆（一次性）
  video_compose.py   # PIL 底图 + ffmpeg 合成 + 字幕
  cursor_client.py   # Cursor Cloud Agents REST + SSE
  batch_aivideo.py   # 选题历史（article_history.json 读写与去重）
  publish_pipeline.py # 生图→合成→YouTube/TikTok API→打印文案→归档
  publish_caption.py  # 统一文案终端展示
  publish_resolve.py # 按视频匹配脚本与封面（API 发布共用）
  publish_youtube.py / youtube_*.py  # YouTube Data API 发布
  publish_tiktok.py / tiktok_*.py    # TikTok Content Posting API 发布
  douyin_*.py / publish_douyin.py    # 抖音（独立调试，不进主流程）
  social_publisher.py # 小红书/视频号（独立调试，不进主流程）
  backfill_social.py # 把存量视频批量补发到其它平台（慎用）
  apply_sau_patches.py # 给 vendor 打兼容补丁（抖音登录 + 小红书话题容错）
```

## 发布策略

**主流程**（`make-and-publish.sh` / `make-us-publish.sh` 共用 `publish_pipeline.py`）：

1. 生图 → 合成
2. **YouTube API** 自动发布（`AIVIDEO_PUBLISH_YOUTUBE=1`，默认开）
3. **TikTok API** 自动发布（`AIVIDEO_PUBLISH_TIKTOK=1`，默认关）
4. **B站 biliup** 自动投稿（`AIVIDEO_PUBLISH_BILIBILI=1`，默认关；需 `./bilibili-login.sh`）
5. 终端打印**一份**通用文案 + 创作者后台链接
5. 归档（`--no-publish` 时不发布、不归档）

国内平台（抖音/小红书/视频号，及雪球/东方财富图文）**仅手动发布**，勿用浏览器脚本自动发帖。

完整渠道对照见 **[docs/PUBLISH_CHANNELS.md](docs/PUBLISH_CHANNELS.md)**。

### YouTube Shorts（主流程，默认开）

```bash
./setup-youtube.sh
./youtube-login.sh
./make-and-publish.sh
./scripts/publish-youtube.sh output/xxx.mp4 --script logs/xxx.json
```

开关：`AIVIDEO_PUBLISH_YOUTUBE=1`。记录：`logs/last_youtube_publish.json`。

### TikTok Direct Post（主流程，默认关）

```bash
./setup-tiktok.sh
# Login Kit redirect: http://127.0.0.1:8765/callback/
./tiktok-login.sh
# .env: AIVIDEO_PUBLISH_TIKTOK=1  TIKTOK_PRIVACY=SELF_ONLY  # 未过审前通常只能私密
./scripts/publish-tiktok.sh output/xxx.mp4 --script logs/xxx.json
```

开关：`AIVIDEO_PUBLISH_TIKTOK=0`。记录：`logs/last_tiktok_publish.json`。

### B站（主流程可选，biliup 视频投稿）

开启 `AIVIDEO_PUBLISH_BILIBILI=1` 后，主流程会用 biliup 自动上传成片。

```bash
./setup-sau.sh
./bilibili-login.sh
# .env: AIVIDEO_PUBLISH_BILIBILI=1
./make-and-publish.sh
./scripts/publish-bilibili.sh output/xxx.mp4 --script logs/xxx.json
```

创作中心：[member.bilibili.com](https://member.bilibili.com/platform/home)。视频默认分区 `BILIBILI_TID=207`（知识·财经商业）。记录：`logs/last_bilibili_publish.json`。

### 国内平台（手动）

| 平台 | 创作者后台 |
|------|------------|
| 抖音 | https://creator.douyin.com/creator-micro/content/upload |
| B站 | https://member.bilibili.com/platform/upload/video/frame（`AIVIDEO_PUBLISH_BILIBILI=1` 可自动） |
| 小红书 | https://creator.xiaohongshu.com/publish/publish?from=homepage |
| 视频号 | https://channels.weixin.qq.com/platform/post/create |
| 雪球（图文） | https://xueqiu.com/ |
| 东方财富（股吧/财富号·图文） | https://mpservice.eastmoney.com/ |
| 知乎专栏（长文） | https://zhuanlan.zhihu.com/write（`AIVIDEO_PUBLISH_ZHIHU=1` + `ZHIHU_AUTO_PUBLISH=1` 可自动发布） |

独立调试（不进主流程，有封号风险）：`./scripts/publish-douyin.sh`、`./scripts/publish-xiaohongshu.sh` 等。

记录：`logs/zh/article_history.json`（选题去重，默认跟随 `AIVIDEO_DAYS` / `BATCH_HISTORY_DAYS`）。

## 各平台账号简介（复制用）

统一定位：**AI财知道｜每天一个 AI 和财经的为什么，A股·美股·港股都聊，用大白话讲清。**

> 合规提示：均已加「不构成投资建议」。财经平台（雪球/富途/东方财富）风控较严，简介中避免“荐股/收益/带单”等字眼。

**昵称统一**：`AI财知道`

**一句话签名（≤20字，B站/微博等短签名）**

```
每天一个 AI 和财经的为什么，大白话讲清。
```

**抖音（个人简介 ≤70 字）**

```
每天用大白话讲清一个 AI 和财经热点，A股·美股·港股都聊。看懂趋势，不追涨杀跌。内容仅为分享，不构成投资建议。
```

**小红书（个人简介，支持换行/emoji；资料审核敏感，避免荐股/收益/带单等字眼）**

```
📈 AI财知道｜每天一个 AI 与财经的为什么

用白板手绘 + 大白话，讲清财报要点、行业脉络与宏观趋势
覆盖 A 股、美股、港股与中概，也聊 AI 如何影响金融与商业

✏️ 人工选题与编排，重信息梳理，轻情绪化表述
⚠️ 内容为知识分享与个人观点，不构成投资建议，请自行判断
```

**小红书头像（审核要求：图中勿含汉字）**：`assets/ai_caizhidao_avatar_xhs.png`（方格纸手绘风，仅英文 AI + 图标；抖音仍可用 `assets/ai_caizhidao_avatar.png`）

**视频号（个人简介 ≤80 字）**

```
每天一个 AI 和财经的为什么。用大白话讲清财报、热点与趋势，A股·美股·港股都聊。看懂 AI 和钱的事，理性看市，不构成投资建议。
```

**B站（个性签名 ≤70 字）**

```
每天一个 AI 和财经的为什么｜财报·热点·趋势用大白话讲清｜A股·美股·港股都聊｜内容仅为分享，不构成投资建议
```

**雪球（个人简介，偏专业 ≤150 字）**

```
AI财知道。聚焦 AI 与财经交叉地带：财报拆解、行业趋势、热点公司基本面，A股、美股、港股与中概股都覆盖。坚持用大白话讲清逻辑与数据，重事实、轻情绪。所有内容仅为信息整理与个人观点分享，不构成任何投资建议，据此操作风险自担。
```

**富途牛牛 / 东方财富股吧（动态/个人简介 ≤120 字）**

```
AI财知道。每天梳理一个 AI 与财经热点，拆解财报与基本面，A股·美股·港股都聊，用大白话讲清逻辑。内容仅为信息分享与个人观点，不构成投资建议，市场有风险，决策请独立判断。
```

## 英文站账号资料（Market Sketch · 复制用）

YouTube / TikTok / Instagram / Facebook / LinkedIn 的昵称、简介、后台链接等，见 **[assets/us-market-profile.md](assets/us-market-profile.md)**（Markdown，代码块可直接复制粘贴）。

头像：`assets/market_sketch_avatar.png` · US 流水线：`.env.en` / `./make-us-publish.sh`

---

## 常用环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `AIVIDEO_MAX_VIDEOS_PER_RUN` | `5`（中文）/ `3`（英文） | 每次生成几条 |
| `AIVIDEO_CURSOR_REUSE_AGENT` | `1` | 同次日更复用 Cloud Agent |
| `AIVIDEO_COMPLIANCE_RELAXED` | `1` | 中文流水线合规略放宽 |

发布成功后，`output/` 里的每条成片会连同**同名图文文件夹**一起移到 `archive/published/YYYYMMDD/`：

```
archive/published/20260531/
  20260531_193024.mp4
  20260531_193024/          ← 发布素材：README.md（视频平台通用文案）、post.md（论坛图文）、cover.jpg、images/
  20260531_192557.mp4
  20260531_192557/
```

合成后自动生成图文包（`AIVIDEO_FORUM_POST=0` 可关闭）。YouTube/TikTok/B站 走 API；雪球/东财/知乎长文可设 `AIVIDEO_PUBLISH_*=1` 自动发布（知乎另设 `ZHIHU_AUTO_PUBLISH=1` 直接点发布）；抖音/小红书/视频号请从归档目录手动发布。

## 环境变量

见 `.env.example`：`CURSOR_*`、`AIHUBMIX_*`（含 `AIHUBMIX_TEXT_MODEL`、`AIHUBMIX_REASONING_EFFORT`、`AIHUBMIX_THINKING_BUDGET`）、`TTS_PROVIDER`、`VOLCENGINE_TTS_*`、`DASHSCOPE_*`、`AIVIDEO_*`、`SAU_*`、`DOUYIN_*`、多平台联动（`AIVIDEO_PUBLISH_XHS/KS/SHIPINHAO`、`XHS_HASHTAGS`、`SAU_XHS_ACCOUNT` 等）。

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
