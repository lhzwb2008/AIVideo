# AIVideo

通过 **扣子编程已部署工作流** 自动生成「AI 60 秒新闻」竖屏视频；本仓库仅负责 API 触发与百炼备用工具。

## 工程目录

```text
AIVideo/
├── .env.example
├── README.md
├── docs/
│   ├── coze-route2-ai-workflow.md   # 建流与部署说明
│   └── coze-vibe-fix-font.md        # 云端字体问题修复
├── scripts/
│   ├── run-coze-vibe-workflow.sh    # 主流程：调用 /run 并下载 MP4
│   ├── test-coze-vibe.sh            # 检测部署 API
│   ├── install-daily-vibe-launchd.sh
│   ├── clean-artifacts.sh
│   ├── test-dashscope.sh            # 百炼连通性
│   └── bailian-chat.sh              # 百炼对话（备用）
├── output/                          # 最终成片（gitignore）
└── logs/                            # 运行元数据（gitignore）
```

## 快速开始

```bash
cp .env.example .env   # 填入 COZE_VIBE_* 与可选 DASHSCOPE_*
chmod +x scripts/*.sh

./scripts/test-coze-vibe.sh
./scripts/run-coze-vibe-workflow.sh "今日AI新闻"
```

成片：`output/YYYYMMDD_HHMMSS.mp4`（约 5–8 分钟）。

## 环境变量

| 变量 | 用途 |
|------|------|
| `COZE_VIBE_RUN_URL` | 部署 API，如 `https://xxx.coze.site/run` |
| `COZE_VIBE_API_TOKEN` | 部署页 API Token |
| `COZE_VIBE_INPUT_KEY` | 请求字段名，默认 `input` |
| `DASHSCOPE_*` | 百炼备用，非主流程 |

## 百炼备用工具

```bash
./scripts/test-dashscope.sh
./scripts/bailian-chat.sh "用三条要点总结今日 AI 新闻"
```

## 定时任务（macOS）

```bash
./scripts/install-daily-vibe-launchd.sh
```

## 故障排查

API 报错 `cannot open resource` → [docs/coze-vibe-fix-font.md](docs/coze-vibe-fix-font.md)，修复后**重新部署**。
