# AI财知道

**每天一个 AI 财经为什么**。从 AI、财经、美股和中概股热点里挑最值得解释的一件事，改编成问句标题的中文短视频。Cursor Cloud Agent 联网搜索 + 深读，Claude Opus 评审与改编，AiHubMix 生图，本地 TTS + ffmpeg 合成竖屏视频，自动发布抖音并联动发布小红书（可选快手 / 视频号），成功后归档。

```bash
./make-and-publish.sh    # 自动选题：生成并自动发布，成功后归档
./make-topics.sh "1 小鹏财报，2 韬定律是什么，3 opus4.8发布"   # 指定话题：逐个生成并发布
./make-from-script.sh script.json   # 直接喂文案：跳过调研/改编，文案直接做视频
./schedule.sh            # 安装/重启每日定时任务
```

## 三种工作模式

| 模式 | 入口 | 文案来源 |
|------|------|----------|
| 自动选题 | `./make-and-publish.sh [N]` | 热点→问句话题（默认 3 条）→ 搜文深读 → 改编 |
| 指定话题 | `./make-topics.sh "<一段含编号的话>"` | 你给定话题，程序联网搜文章/用自带内容 → 自动改编 |
| 直接喂文案 | `./make-from-script.sh script.json` | 你（或模型）按生图要求写好分页文案，**跳过调研/改编** |

前两种都会走「找/给材料 → Opus 改编成脚本」的链路；**直接喂文案模式**把后半段的「生图 + 合成视频 + 发布」单独抽出来，适合那些 `make-topics` 满足不了、需要你完全掌控逐页文案的特殊话题/场景。

### 指定话题模式

直接运行 `./make-topics.sh` 即可，**默认读取项目根目录的 `topics.txt`**（无需任何参数）。`topics.txt` **每行一个话题**，行首可写栏目名手动指定分类（不用方括号），例如：

```
基础 如何给企业估值
港美股 港股通怎么开通
AI GLM5.1对比Qwen3.7
```

行首栏目名支持 `基础 / A股 / 港美股 / AI / 量化`（也兼容 `[基础]` 方括号写法）；不写栏目则按内容自动判定。仍可用 `--file other.txt` 指定别的清单，或把话题直接作参数/stdin 传入（单行用 `1 2 3` 编号或顿号分隔）。每个话题自动走三选一：

1. **自带内容**：编号后用「：」跟一大段已整理好的文字（如「4 蔚小理净现金对比：<数据与结论>」）→ 直接把这段内容当原文深读、改编成脚本，不再联网。
2. **普通话题词**（如「小鹏财报」「opus4.8发布」）→ 用 Cursor/Exa 联网搜热门文章，深读最相关一篇；科普向话题（含「是什么/为什么/原理/定律」等）优先搜通俗易懂的科普文。
3. **搜不到合适文章** → 让模型用自身可靠知识写科普向细节，再改编。

```bash
./make-topics.sh                                       # 回车后按提示在命令行输入话题
./make-topics.sh "1 小鹏财报 2 韬定律是什么 3 opus4.8发布"
./make-topics.sh --no-publish "1 小鹏财报 2 韬定律是什么"   # 只生成不发布
./make-topics.sh --dry-run   "1 小鹏财报"                  # 预演发布参数不真发
./make-topics.sh --file topics.txt                          # 从文件读
echo "1 小鹏财报 2 韬定律是什么" | ./make-topics.sh -        # 从 stdin 读
```

可选环境变量：`AIVIDEO_TOPIC_DAYS`（搜话题文章的时间窗，默认 120 天）。

### 直接喂文案模式

当某些特殊话题/场景下 `make-topics` 的「搜文章 → 深读 → 改编」满足不了需求时，你（或模型）可以**自己按生图要求把逐页文案写好**，用这个模式直接生图 → 合成 → 发布，跳过整个调研/改编链路。

文案是一个 JSON 文件，结构与改编后的脚本一致（模板见 `assets/script-template.json`）：

```json
{
  "title": "换手率高到底是好事还是坏事？",
  "keyword": "换手率",
  "hashtags": ["换手率", "A股", "炒股入门"],
  "category": "basic",
  "slides": [
    {
      "headline": "换手率到底是啥",
      "narration": "……口播文案……",
      "image_prompt": "a theater with 1000 seats, a percentage meter ...",
      "on_image_text": ["换手率=换了多少手", "剧院1000座位", "300人换座=30%"],
      "subtitle": "换手率到底是啥"
    }
  ]
}
```

字段约定（与自动改编后的脚本同一套校验，不合规会直接报错并指出哪一页哪个字段）：

- `title` 4-30 字；`keyword`/`hashtags`/`category` 可选（缺省自动取/判定，`hashtags` 最多 5 个）。
- `slides` 至少 3 页，最多 `AIVIDEO_MAX_SLIDES`（默认 4）页；**第 1 页自动当封面（cover），其余为正文（body）**。
- 每页必填 `headline`（≤14 字）、`narration`（封面 40-120 字 / 正文 50-220 字）、`image_prompt`（英文画面描述）、`on_image_text`（3-12 条，每条 ≤16 字）。
- 可选 `chapter_title`（2-6 字，缺省由 headline 推导）；封面页可填 `subtitle`（6-24 字），正文页可填 `lead_in`（≤14 字），缺省都会自动补。
- 顶层若是数组 `[ {...}, {...} ]` 则视为多条脚本，逐条制作发布。
- 同样遵守合规红线（禁股票代码/荐股/喊单等）。

```bash
./make-from-script.sh script.json                 # 制作并发布（推荐）
./make-from-script.sh script.json --no-publish     # 只生成不发布
./make-from-script.sh script.json --dry-run        # 预演发布参数不真发
cat script.json | ./make-from-script.sh -          # 从 stdin 读文案
```

> 提示：你也可以直接让我（AI）按上面的字段约定把某个话题写成 `script.json` 再跑这条命令，这样能完全控制每一页的口播和画面。

## 内容策略

**每日自动（`./make-and-publish.sh`）与指定话题（`./make-topics.sh`）共用同一套制作形态**（A/B 实验已并入主流程）：

1. **定话题**：自动模式从近 7 天热点里提炼 6–8 条问句线索，再按 **`AIVIDEO_DIR_RATIO` 比例 + `AIVIDEO_ASTOCK_MIN_RATIO`（默认 A股>50%）** 从 `AIVIDEO_MAX_VIDEOS_PER_RUN` 条里选出；手动模式见 `topics.txt`。
2. **搜文 + 深读**：按话题线索 Exa 搜最相关且够新的文章（默认 7 天窗口；新闻类超过 `AIVIDEO_TOPIC_FRESH_DAYS` 会综合多篇材料自写）。
3. **改编脚本**（Claude Opus）：拆 **3-4 页正文** + 封面海报；问句标题；结尾引导评论。
4. **风格校验 + 合规红线**（禁股票代码/荐股等，见 `research.py`）。

旧流程「先给几十篇文章逐条打 0–100 分再挑一篇」已移除，改为 **话题优先**，时效与可讲性在「提炼问句」阶段一次完成。

## 子栏目（同一主账号下的频道）

主账号 `AI财知道` 下分 5 个子栏目，只用**不同主题色 + 角标后缀**做视觉区分（角标显示成「AI财知道 · A股」等），定义见 `src/categories.py`：

| 栏目 | key | 主题色 | 如何归类 |
|------|-----|--------|----------|
| A股 | `astock` | 暖红 | 自动：命中涨停/科创板/沪深等 A股 信号 |
| 港美股 | `hkus` | 蓝 | 自动：命中美股/港股/中概/英伟达等信号 |
| AI资讯 | `ai` | 紫 | 自动：命中大模型/GPT/Claude/智能体等且与具体行情无关 |
| 量化 | `quant` | 绿 | **不改选题逻辑**，人工用 topics 指定 |
| 基础 | `basic` | 青 | 自动（兜底）：讲通用财经概念/原理（市盈率、估值、K线、复利等「是什么/怎么算」类科普） |

- **自动选题/普通话题**：合成时按脚本内容自动判定栏目（A股 > 港美股 > AI资讯 > 基础），判不出则用默认黄色主题。
- **手动指定栏目（量化等）**：在 `make-topics` 的话题前加 `[栏目]` 标签即可，例如：

```bash
./make-topics.sh "1 [量化] 多因子选股是什么 2 [A股] 今天为什么大涨"
```

- 也可整轮强制某栏目：`AIVIDEO_CATEGORY=quant ./make-topics.sh "..."`。
- 栏目会写进脚本的 `category` 字段，被 `video_compose` 读出用于徽标/封面/尾页配色。

## 视觉风格

- **白板手绘** 方格纸底，黑色钢笔线 + 黄/紫荧光笔点缀
- **9:16 竖版**：图片占顶部 78%，底部 22% 留给字幕
- 中文短语（`on_image_text`）作为手写注释画进图里
- 章节角标 + 页码

## 流程

| 步骤 | 命令 | 产出 |
|------|------|------|
| Make + publish | `./make-and-publish.sh` | 热点→问句话题（默认 3 条）+ 搜文深读 + 改编 + 生图 + 合成 + 发布 + 归档 |
| Make from topics | `./make-topics.sh` | 指定话题 + 改编 + 生图 + 合成 + 发布 + 归档 |
| Make from script | `./make-from-script.sh script.json` | **跳过调研/改编**，现成文案 + 生图 + 合成 + 发布 + 归档 |
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
  research.py        # 核心管线：找文章 → 评审 → 深读 → 改编（含校验+修正轮）
  specified_topics.py# 指定话题模式：解析输入 + 按 自带内容/搜文章/模型自写 三路出 article+details
  make_topics_publish.py # 指定话题一键制作发布编排（生图/合成/发布流水线 pipeline_after_script）
  make_from_script.py    # 直接喂文案模式：现成分页文案归一化+校验后直接走生图/合成/发布流水线
  text_client.py     # AiHubMix chat（thinking 预算最低）
  image_client.py    # AiHubMix gpt-image-2 生图（含重试）
  enrich_images.py   # 逐页生图，断点续跑
  tts_client.py      # TTS 分流：豆包声音复刻 / 百炼 CosyVoice
  voice_clone.py     # 百炼音色克隆（一次性）
  video_compose.py   # PIL 底图 + ffmpeg 合成 + 字幕
  cursor_client.py   # Cursor Cloud Agents REST + SSE
  batch_aivideo.py   # 批量编排（近 7 天 URL + 标题/主题去重）
  make_publish.py    # 自动选题一键制作发布编排（含抖音成功后联动多平台 publish_social）
  douyin_*.py / sau_client.py / publish_douyin.py  # 抖音发布（独立 patchright）
  douyin_caption.py  # 抖音标题/简介/话题生成
  social_caption.py  # 小红书/快手/视频号风格文案生成（复用 douyin_caption）
  social_publisher.py# 统一多平台发布器（直连 vendor/social-auto-upload 的 uploader）
  backfill_social.py # 把抖音存量视频批量补发到其它平台
  apply_sau_patches.py # 给 vendor 打兼容补丁（抖音登录 + 小红书话题容错）
```

## 抖音发布（与制作分离）

抖音无开放 API，需 Playwright + 扫码 cookie。

```bash
./setup-sau.sh                       # 一次性
./douyin-login.sh                    # 扫码登录
./make-and-publish.sh                # 正常入口：制作完成后自动发布
./scripts/publish-douyin.sh output/xxx.mp4   # 单条调试
```

记录：`logs/published_videos.json`、`logs/video_manifest.jsonl`、`logs/article_history.json`。其中 `article_history.json` 会保留近期已做标题，默认近 7 天用于选题去重；可用 `BATCH_HISTORY_DAYS` 调整。

## 多平台发布（小红书 / 快手 / 视频号）

复用 `vendor/social-auto-upload`，与抖音一套工具。主流程会在抖音发布成功后，按 `.env` 开关顺手联动发布（best-effort，失败不阻断抖音）。

```bash
./social-login.sh xiaohongshu          # 扫码登录（kuaishou / shipinhao 同理）
./social-login.sh xiaohongshu --check  # 校验登录态
./scripts/publish-xiaohongshu.sh output/xxx.mp4 --script logs/xxx.json   # 单条调试
./scripts/backfill-social.sh xiaohongshu --dry-run   # 把抖音存量批量补发到小红书
```

开关：`AIVIDEO_PUBLISH_XHS`（默认 1）、`AIVIDEO_PUBLISH_KS`、`AIVIDEO_PUBLISH_SHIPINHAO`。各平台已发记录：`logs/published_<platform>.json`。

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

**快手（个人简介 ≤60 字）**

```
每天一个 AI 和财经的为什么，A股·美股·港股都聊，用大白话讲清。内容仅供参考，不构成投资建议。
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

## 每日定时（macOS launchd）

每天定时跑一遍「搜近 7 天热点 → 提炼 3 条问句话题 → 制作发布 → 归档」：

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
| `DAILY_RUN_COUNT` | `3` | 定时任务每次生成几条（同 `AIVIDEO_MAX_VIDEOS_PER_RUN`） |
| `DAILY_RUN_DAYS` / `AIVIDEO_DAYS` | `7` | 发现热点候选的时间窗（天） |
| `AIVIDEO_TOPIC_DAYS` | `7` | 每条话题搜文的时间窗（天） |
| `AIVIDEO_DIR_RATIO` | `0.55,0.25,0.20` | 三方向目标占比 `astock,ai,hkus`（归一化后分配条数） |
| `AIVIDEO_ASTOCK_MIN_RATIO` | `0.5` | A股条数须 **严格大于** 该占比（3 条→至少 2 条 A股） |
| `AIVIDEO_TOPIC_FRESH_DAYS` | `2` | 新闻类：候选超过该天数则改综合材料自写 |

发布成功的视频会被移到 `archive/published/YYYYMMDD/`，第二天 `output/` 又是干净的等待新一批。

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
