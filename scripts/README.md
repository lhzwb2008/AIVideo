# 脚本

## 主流程（Coze 已部署）

| 脚本 | 说明 |
|------|------|
| `run-coze-vibe-workflow.sh` | 调用 `*.coze.site/run`，下载 `output/*.mp4` |
| `test-coze-vibe.sh` | 检测 `graph_parameter` |
| `install-daily-vibe-launchd.sh` | 每天 8:00 自动跑 |
| `clean-artifacts.sh` | 清理过程文件 |

## 百炼备用

| 脚本 | 说明 |
|------|------|
| `test-dashscope.sh` | 检测 Key / 白名单 |
| `bailian-chat.sh` | OpenAI 兼容对话，例：`./scripts/bailian-chat.sh "你好"` |
