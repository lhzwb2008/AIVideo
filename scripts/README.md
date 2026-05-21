# 脚本说明

| 脚本 | 用途 |
|------|------|
| `build-slideshow-video.sh` | **推荐** 幻灯片式 AI 新闻视频（百炼 + 万相 + FFmpeg） |
| `build_slideshow.py` | 同上（Python 入口，由 shell 调用） |
| `run-coze-workflow.sh` | 触发 Coze `aivideo` 工作流并下载 MP4 |
| `test-dashscope.sh` | 检测百炼 Key / IP 白名单 |
| `test-credentials.sh` | 检测 `.env` 中 Coze SAT 与百炼 |
| `install-daily-launchd.sh` | 安装每天 8:00 自动跑 `run-coze-workflow.sh` |

首次使用：

```bash
cp .env.example .env
chmod +x scripts/*.sh
./scripts/test-dashscope.sh
./scripts/build-slideshow-video.sh "今日AI新闻"
```
