# AIVideo

**每日一个 AI 热点** → Cursor 调研 → Coze 合成竖屏视频 →（可选）Playwright 发布抖音。

```bash
./run-aivideo.sh
```

## 内容策略

1. Cursor 联网搜索，**锁定当天 1 个 AI 热点关键词**
2. 找到 1 篇精华文章，**复述文章观点**（不加自编观点）
3. 5 页 PPT 深度展开：抛题 → 三节论点 → 收束
4. `title` / `keyword` 突出搜索词，便于抖音被关键词搜到

## 流程

| 步骤 | 命令 | 产出 |
|------|------|------|
| 一键全流程 | `./run-aivideo.sh` | 调研 + 合成 +（可选）发布 |
| 调研 | `python3 lib/research.py "今日AI新闻"` | `logs/last_script.json` |
| 合成 | `./run-coze.sh` | `output/*.mp4` |
| 发布 | `python3 publish-douyin.py` | 抖音创作者平台 |

JSON 含 `title`、`keyword`、`source`（参考文章链接）、5 页 `slides`。

## 抖音发布

不依赖抖音开放平台 API。发布用 **Playwright** 自动化；登录仍通过 **social-auto-upload（sau）** 扫码保存 cookie。

### 一次性安装

```bash
./setup-sau.sh
./douyin-login.sh   # 扫码登录
```

### 手动发布

```bash
python3 publish-douyin.py
python3 publish-douyin.py --dry-run
python3 publish-douyin.py output/xxx.mp4
python3 publish-douyin.py --assist   # 半自动：脚本填表，手动点发布
```

### 全自动流水线

`.env` 中设置 `DOUYIN_AUTO_PUBLISH=1`，`run-aivideo.sh` 合成完成后自动发布。

## 项目结构

```
run-aivideo.sh      # 主入口
run-coze.sh         # 仅 Coze 合成
publish-douyin.py   # 发布抖音
douyin-login.sh     # 抖音扫码登录（sau）
setup-sau.sh        # 安装 sau + patchright（一次性）
clean-logs.sh       # 清理调试日志
lib/                # Python 模块
logs/ output/       # 运行产物
vendor/             # social-auto-upload（setup-sau 后生成）
```

## 环境变量

见 `.env.example`（`CURSOR_*`、`COZE_VIBE_*`、`SAU_*`、`DOUYIN_*`）
