# Windows 服务器部署指南（make-and-publish 中文流水线）

在 Windows Server 上每日自动制作并发布「AI财知道」视频。本文是**拉代码后的完整 TODO**。

---

## 一、前置条件

| 项目 | 要求 |
|------|------|
| 系统 | Windows Server 2019+ 或 Windows 10/11 |
| 权限 | 管理员（安装依赖、注册计划任务） |
| 网络 | 可访问 GitHub、AiHubMix、火山 TTS、各创作者平台 |
| 桌面 | **首次登录 cookie 必须 RDP 进桌面会话**（扫码/有头 Chrome） |
| 账号 | Cursor API、AiHubMix API、火山 TTS 已开通 |

---

## 二、一键安装

```powershell
cd C:\path\to\AIVideo
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\setup-windows.ps1 -UseWinget
```

脚本会安装/检测：Git、Python 3.10–3.12、ffmpeg、Chrome、主 `.venv`、`vendor/social-auto-upload`、patchright Chromium。

---

## 三、部署 TODO 清单

按顺序勾选：

### 1. 拉代码

- [ ] `git clone <repo> C:\AIVideo`（或 `git pull` 更新）
- [ ] 确认 `assets/`、`assets/bgm/`、字体文件存在

### 2. 运行安装脚本

- [ ] `.\setup-windows.ps1 -UseWinget`
- [ ] 确认输出末尾 `冒烟测试 OK`

### 3. 填写 `.env`

从 `.env.example` 复制（安装脚本已自动复制则跳过）。**必改 shared 段：**

- [ ] `CURSOR_API_KEY`
- [ ] `CURSOR_SANDBOX_REPO_URL`（Cloud Agent 沙箱仓库）
- [ ] `AIHUBMIX_API_KEY`
- [ ] `VOLCENGINE_TTS_API_KEY` + `VOLCENGINE_TTS_SPEAKER`（豆包克隆音色）
- [ ] `WECHAT_APP_ID` / `WECHAT_APP_SECRET`（若开公众号）

**`#== section: zh ==` 发布开关（全渠道自动发布推荐配置）：**

```ini
# 视频平台
AIVIDEO_PUBLISH_BILIBILI=1
AIVIDEO_PUBLISH_DOUYIN=1
AIVIDEO_PUBLISH_XHS=1
AIVIDEO_PUBLISH_SHIPINHAO=1

# 论坛图文
AIVIDEO_PUBLISH_EASTMONEY=1
AIVIDEO_PUBLISH_XUEQIU=1
AIVIDEO_PUBLISH_ZHIHU=1
ZHIHU_AUTO_PUBLISH=1

# 公众号（默认仅存草稿，需手动点发表）
AIVIDEO_PUBLISH_WECHAT=1
WECHAT_DRAFT_ONLY=1
```

- [ ] 根据实际上线渠道调整开关（未开的渠道不会发布）
- [ ] 确认 `LLM_BROWSER_MODEL=claude-opus-4-8`（抖音/小红书/视频号 LLM 填表）
- [ ] 确认 `AIVIDEO_MAX_VIDEOS_PER_RUN`（工作日 5 / 周末 3）

### 4. 登录各平台 Cookie（RDP 桌面内执行）

> 计划任务在 Session 0 无桌面时，**浏览器扫码登录必须在交互式 RDP 中完成一次**。Cookie/Profile 持久化后可 headless 发布。

| 平台 | 命令 | 说明 |
|------|------|------|
| B站 | `.\scripts\login-cn.ps1 bilibili` | biliup 扫码 |
| 抖音 | `.\scripts\login-cn.ps1 douyin` | Chrome 扫码 |
| 小红书 | `.\scripts\login-cn.ps1 xiaohongshu` | Chrome 扫码 |
| 视频号 | `.\scripts\login-cn.ps1 shipinhao` | 微信扫码 |
| 东方财富 | `.\scripts\login-cn.ps1 eastmoney` | 创作平台登录 |
| 雪球 | `.\scripts\login-cn.ps1 xueqiu` | 登录态 |
| 知乎 | `.\scripts\login-cn.ps1 zhihu` | 若 `AIVIDEO_PUBLISH_ZHIHU=1` |

校验（不打开浏览器）：

```powershell
.\scripts\login-cn.ps1 douyin --check
.\scripts\login-cn.ps1 xiaohongshu --check
.\scripts\login-cn.ps1 shipinhao --check
```

- [ ] 所有已开启渠道的 `--check` 均通过
- [ ] Cookie 位于 `vendor\social-auto-upload\cookies\`
- [ ] LLM 平台 Profile 位于 `vendor\social-auto-upload\cookies\browser_profiles\`

### 5. 试跑流水线

```powershell
# 只跑 1 条，验证制作+发布全链路
.\make-and-publish.ps1 1

# 只制作不发布
.\make-and-publish.ps1 1 --no-publish
```

- [ ] 生图、TTS、ffmpeg 合成成功 → `output\zh\*.mp4`
- [ ] 各平台发布日志 → `logs\zh\last_*_publish.json`
- [ ] 归档 → `archive\published\YYYYMMDD\zh\`

### 6. 注册每日计划任务

```powershell
# 每天 08:00 跑默认条数（工作日 5 / 周末 3）
.\scripts\register-daily-publish.ps1 -At 08:00

# 或固定 3 条
.\scripts\register-daily-publish.ps1 -At 07:30 -Count 3
```

- [ ] 任务名 `AIVideoMakeAndPublish` 已创建
- [ ] 手动触发一次：`Start-ScheduledTask -TaskName AIVideoMakeAndPublish`
- [ ] 确认任务以**同一用户**运行（该用户已 RDP 登录过并完成 cookie）

### 7. 运维

- [ ] 日志目录 `logs\zh\` 定期备份
- [ ] Cookie 过期时 RDP 重登：`.\scripts\login-cn.ps1 <platform> --force`
- [ ] 代码更新后：`git pull` → `.\setup-windows.ps1 -SkipChromium`（仅更新依赖）
- [ ] 磁盘空间：`archive\published\` 与 `output\zh\` 监控

---

## 四、make-and-publish 渠道对照

| 渠道 | .env 开关 | 登录方式 | 发布方式 |
|------|-----------|----------|----------|
| B站视频 | `AIVIDEO_PUBLISH_BILIBILI=1` | biliup 扫码 | API 自动 |
| 抖音 | `AIVIDEO_PUBLISH_DOUYIN=1` | Chrome Profile | LLM 浏览器 |
| 小红书 | `AIVIDEO_PUBLISH_XHS=1` | Chrome Profile | LLM 浏览器 |
| 视频号 | `AIVIDEO_PUBLISH_SHIPINHAO=1` | Chrome Profile | LLM 浏览器 |
| 东方财富 | `AIVIDEO_PUBLISH_EASTMONEY=1` | Playwright 登录 | 图文自动 |
| 雪球 | `AIVIDEO_PUBLISH_XUEQIU=1` | Playwright 登录 | 图文自动 |
| 知乎专栏 | `AIVIDEO_PUBLISH_ZHIHU=1` + `ZHIHU_AUTO_PUBLISH=1` | Playwright 登录 | 自动点发布 |
| 微信公众号 | `AIVIDEO_PUBLISH_WECHAT=1` | API（AppId/Secret） | 默认草稿箱 |

**不上传自定义封面**（各平台用视频首帧）。

---

## 五、常见问题

**Q: 计划任务跑了但浏览器发布失败？**  
A: 确认 cookie/profile 存在；LLM 发布需 headless Chromium，首次必须在 RDP 桌面完成登录。

**Q: ffmpeg 找不到？**  
A: `winget install Gyan.FFmpeg`，重开 PowerShell，或把 `ffmpeg.exe` 目录加入系统 PATH。

**Q: Python 3.13 报错？**  
A: SAU 仅支持 3.10–3.12，用 `py -3.12` 或安装 3.12 后重跑 `setup-windows.ps1`。

**Q: 和 macOS 脚本区别？**  
A: Windows 用 `make-and-publish.ps1` / `setup-windows.ps1`；`publish_pipeline` 在 Windows 下自动直调 Python，无需 Git Bash。

---

## 六、快速命令索引

```powershell
.\setup-windows.ps1 -UseWinget          # 安装依赖
.\make-and-publish.ps1                  # 默认条数全流水线
.\make-and-publish.ps1 1 --no-publish   # 试制不发布
.\scripts\login-cn.ps1 douyin --check   # 校验登录
.\scripts\register-daily-publish.ps1 -At 08:00
```
