# AIVideo

1 分钟 AI 新闻短视频 / 播客流水线（本地开发）。

## 工程目录

```text
AIVideo/
├── .env.example          # 环境变量模板（复制为 .env）
├── requirements.txt      # Python 依赖
├── README.md
├── docs/                 # 文档（含 Coze 工作流改版说明）
├── scripts/              # 可执行脚本
│   ├── build_slideshow.py       # 幻灯片流水线主程序
│   ├── build-slideshow-video.sh   # 一键生成幻灯片视频
│   ├── run-coze-workflow.sh       # 调用 Coze 已发布工作流
│   ├── test-dashscope.sh          # 百炼 API 诊断
│   ├── test-credentials.sh        # Coze + 百炼连通性
│   └── install-daily-launchd.sh   # macOS 定时任务（可选）
├── output/               # 成片与中间文件（已 gitignore，本地自动生成）
└── logs/                 # 定时任务日志（已 gitignore）
```

## 密钥配置（必读）

**切勿把 `.env` 或真实密钥提交到 Git。** 若曾在聊天、截图、仓库里暴露过密钥，请立即在对应控制台**作废并重新生成**：

- Coze：API & SDK → 授权 → 服务身份凭证 → 删除旧 SAT → 新建
- 百炼：DashScope 控制台 → API Key 管理 → 删除旧 Key → 新建

### 本地步骤

```bash
cp .env.example .env
# 编辑 .env，填入新密钥（不要用聊天里发过的旧密钥）
chmod +x scripts/test-credentials.sh
./scripts/test-credentials.sh
```

### 两把钥匙分别做什么

| 变量 | 用途 |
|------|------|
| `COZE_API_TOKEN` | Coze **服务身份 SAT**，`Authorization: Bearer sat_...`，调用[已发布工作流](https://www.coze.cn/api/open/docs/developer_guides/workflow_run) |
| `COZE_WORKFLOW_ID` | 你在扣子编排并**发布**的工作流 ID（URL 里 `workflow_id=` 后） |
| `DASHSCOPE_*` | 百炼 OpenAI 兼容接口：写稿、对话；TTS/图像可走同账号其他 DashScope API |

SAT 需勾选 **run** 权限，且令牌能访问该工作流所在**空间**。

### 调用 Coze 工作流示例

```bash
curl -X POST 'https://api.coze.cn/v1/workflow/run' \
  -H "Authorization: Bearer $COZE_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"workflow_id\":\"$COZE_WORKFLOW_ID\",\"parameters\":{\"topic\":\"今日AI新闻\"}}"
```

### 百炼写稿示例

```bash
curl "${DASHSCOPE_BASE_URL}/chat/completions" \
  -H "Authorization: Bearer $DASHSCOPE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen-plus","messages":[{"role":"user","content":"用60秒口播稿总结今日AI三条新闻"}]}'
```

## 推荐架构

- **百炼**：选题 / 60 秒脚本 / 播客长稿  
- **Coze 工作流**：视频合成、插件（如 AutoAI 分发）、或对接龙虾 Skill  
- **发布**：工作流内插件或 OpenClaw Skill，见前期调研说明  

## 幻灯片式成片（推荐，先图后视频）

本地流水线（不依赖 Coze 文生视频）：

```bash
./scripts/build-slideshow-video.sh "今日AI新闻"
```

成片：`output/slideshow_*.mp4`（1080×1920，约 60 秒，PPT 资讯风）。

在 Coze 里按同样逻辑改版见：[docs/coze-workflow-v2-slideshow.md](docs/coze-workflow-v2-slideshow.md)

> 若百炼 Key 开启了 **IP 白名单**，需在控制台放行本机 IP，否则本地脚本会退回模板分镜 + 纯色底图；在 Coze 工作流内调用百炼不受此限制。

## 工作流（已对接 · 旧版 Seedance 直出）

- 名称：`aivideo`
- 链接：[工作流编排页](https://www.coze.cn/work_flow?workflow_id=7641981453953204264&space_id=7522025257893412902)
- 入参：`input`（string，可选）
- 出参：`output`（video URL）

### 一键生成视频（无需打开 Coze）

```bash
./scripts/run-coze-workflow.sh
# 或指定主题
./scripts/run-coze-workflow.sh "OpenAI 发布新模型"
```

成片保存在 `output/YYYYMMDD_HHMMSS.mp4`，元数据在 `output/last_run.json`。

### macOS 每天自动跑（可选）

```bash
chmod +x scripts/*.sh
./scripts/install-daily-launchd.sh
```

## 发布到抖音/小红书

当前工作流**只生成视频**，不包含发帖。要在 API 链路里自动发布，需在 Coze 工作流末尾增加 **AutoAI 一键发布** 等插件节点，或另接 OpenClaw 发布 Skill。
