# AIVideo

**每日一个 AI 热点** → Cursor 调研 → Coze 合成竖屏视频 →（手动）发布抖音。

```bash
./run-aivideo.sh
./publish-all-douyin.sh    # 制作完成后单独执行
```

## 内容策略

1. Cursor 联网搜索，**锁定当天 1 个大众向 AI 热点**（产品发布、翻车、涨价等，避开 IPO 交表等专业题）
2. 基于 1 篇权威报道提取事实，**口语化口播**（比喻、调侃 OK，不编造数字）
3. 封面 **8–14 字大标题** + 悬念副标题；5 页：抛题 → 三节论点 → 收束
4. `title` / `keyword` 突出搜索词；脚本过 **风格校验**（禁通稿腔，失败自动让 Agent 修正一轮）

## 流程

| 步骤 | 命令 | 产出 |
|------|------|------|
| 一键制作 | `./run-aivideo.sh` | 调研 + **API 生图** + Coze 合成 → `output/*.mp4` |
| 批量制作 | `./run-batch-aivideo.sh` | 多条视频（默认 10 条 / 近 30 天） |
| 生图 | `./run-enrich-images.sh` | 为脚本 slides 写入 `image_url` |
| 合成 | `./run-coze.sh` | `output/*.mp4`（TTS + 视频，不再内部生图） |
| **发布** | `./publish-all-douyin.sh` | 发布 `output/` 下未发布的 MP4 |

JSON 含 `title`、`keyword`、`source`（参考文章链接）、5 页 `slides`。

## 抖音发布（与制作分离）

制作流程**不包含**发布。抖音无开放 API，需 Playwright + 扫码 cookie，适合**制作完后手动发布**。

### 一次性安装

```bash
./setup-sau.sh
./douyin-login.sh   # 扫码登录（发布前执行）
```

### 批量发布 output/

```bash
./publish-all-douyin.sh              # 发布全部未发布的 mp4
./publish-all-douyin.sh --dry-run    # 预览待发布列表
./publish-all-douyin.sh --assist     # 半自动：脚本填表，你点「发布」
```

已发布记录：`logs/published_videos.json`；视频↔脚本映射：`logs/video_manifest.jsonl`。

### 单条发布

```bash
./publish-douyin.sh output/xxx.mp4
./publish-douyin.sh --assist
```

## 项目结构

```
run-aivideo.sh          # 制作：调研 + API 生图 + Coze 合成
run-batch-aivideo.sh    # 批量制作
run-enrich-images.sh    # 仅 AiHubMix 生图（gpt-image-2）
run-coze.sh             # 仅 Coze 合成
publish-all-douyin.sh   # 批量发布 output/
publish-douyin.sh       # 单条发布
douyin-login.sh         # 抖音扫码登录
setup-sau.sh            # 安装 sau（一次性）
clean-logs.sh           # 清理调试日志
src/                    # 全部 Python 代码
logs/ output/           # 运行产物
vendor/                 # social-auto-upload
```

## 环境变量

见 `.env.example`（`CURSOR_*`、`AIHUBMIX_*`、`COZE_VIBE_*`、`SAU_*`、`DOUYIN_*`）

### 生图拆分（AiHubMix → Coze）

1. 本地 `./run-enrich-images.sh` 用 **gpt-image-2** 按每页 `image_prompt` 生图，上传到 catbox，写入 `slide.image_url`
2. Coze 工作流需整体重构：**只负责 TTS + 视频合成**，不再内部生图

完整 Coze 改造 Prompt（复制到 code.coze.cn 项目 AI）：**[`coze-workflow-prompt.txt`](coze-workflow-prompt.txt)**
