# 发布渠道一览

> 更新：2026-06-01  
> 背景：小红书等平台已明确禁止「用第三方脚本/AI 模拟真人发帖互动」。主流程改为 **YouTube 官方 API 自动发布 + 国内平台手动发布**。

## 主流程行为（`make-and-publish.sh` / `make-topics.sh` / `make-from-script.sh`）

1. 生成竖屏视频  
2. **YouTube**：若 `AIVIDEO_PUBLISH_YOUTUBE=1`（默认），走 YouTube Data API 自动上传  
3. **TikTok**：若 `AIVIDEO_PUBLISH_TIKTOK=1`，走 TikTok Content Posting API Direct Post  
4. **终端打印**：一份通用标题/简介/话题 + 各平台创作者后台链接  
5. 归档到 `archive/published/YYYYMMDD/`（`--no-publish` 时不发布、不归档）

---

## 自动发布（官方 API，相对安全）

| 平台 | 方式 | API 费用 | 接入难度 | 备注 |
|------|------|----------|----------|------|
| **YouTube Shorts** | ✅ 已接入主流程 | **免费** | `./setup-youtube.sh` → `./youtube-login.sh` |
| **TikTok** | ✅ 已接入（默认关） | **免费**（需 App Review；未过审通常仅 `SELF_ONLY`） | `./setup-tiktok.sh` → `./tiktok-login.sh` → `AIVIDEO_PUBLISH_TIKTOK=1` |
| **Instagram Reels** | 未接入 | Meta 开发者免费层 + 商业账号要求 | 高 | [Instagram Graph API](https://developers.facebook.com/docs/instagram-api/) Content Publishing，需 Facebook 商业账号/页面关联 |
| **Facebook Reels** | 未接入 | 同上 | 高 | Meta Graph API |
| **LinkedIn** | 未接入 | 依应用类型 | 高 | Marketing API 视频上传，需 Partner 审核 |

**不推荐当作「官方 API」的：**

| 方式 | 风险 |
|------|------|
| Playwright / Selenium 模拟浏览器发帖 | 已被小红书等平台明确打击（AI 托管、非真实互动） |
| 逆向/非公开接口 + Cookie | 随时失效，封号风险高 |
| 第三方「一键分发」SaaS | 底层多为浏览器自动化，风险同上 |

---

## 手动发布（无可靠开放 API，请真人上传）

| 平台 | 创作者后台（收藏用） | 开放上传 API | 说明 |
|------|----------------------|--------------|------|
| **抖音** | https://creator.douyin.com/creator-micro/content/upload | ❌ 无个人开放 API | 企业/机构有内容分发合作，不对普通创作者开放 |
| **小红书** | https://creator.xiaohongshu.com/publish/publish?from=homepage | ❌ 无 | 开放平台偏电商/服务商；**严禁脚本模拟发帖** |
| **快手** | https://cp.kuaishou.com/article/publish/video | ⚠️ 仅合作伙伴 | 普通创作者需手动或官方 App |
| **视频号** | https://channels.weixin.qq.com/platform/post/create | ❌ 无 | 微信生态内手动发布 |
| **B 站** | https://member.bilibili.com/platform/upload/video/frame | ⚠️ 非公开 OAuth | 开放平台主要面向小程序/直播等；视频投稿多为 Cookie 级非官方接口，有封号风险 |
| **微博** | https://weibo.com/ | ❌ 无短视频专用 API | 视频随博文手动发 |
| **TikTok（未接 API 时）** | https://www.tiktok.com/upload | 见上「自动发布」 | 可手动；接 API 后改自动 |
| **西瓜视频** | https://studio.ixigua.com/ | ❌ 无 | 字节系，与抖音账号体系相关 |

---

## 项目内脚本对照

| 用途 | 命令 |
|------|------|
| 每日自动选题 + 制作 + YouTube | `./make-and-publish.sh` |
| 指定话题 | `./make-topics.sh` |
| 直接喂文案 | `./make-from-script.sh script.json` |
| 只生成不发 | 以上任一脚本加 `--no-publish` |
| YouTube 授权 | `./youtube-login.sh` |
| YouTube 单条调试 | `./scripts/publish-youtube.sh output/xxx.mp4 --script logs/xxx.json` |
| TikTok 授权 | `./tiktok-login.sh` |
| TikTok 单条调试 | `./scripts/publish-tiktok.sh output/xxx.mp4 --script logs/xxx.json` |
| 国内平台单条调试（**不进主流程，仍有封号风险**） | `./scripts/publish-xiaohongshu.sh` 等 |
| 补发历史（同上，慎用） | `./scripts/backfill-social.sh` |

---

## 后续可调研接入优先级（建议）

1. **TikTok** — 已接入，过 App Review 后可公开；默认 `TIKTOK_PRIVACY=SELF_ONLY`  
2. **Instagram Reels** — 需 Meta 商业账号，流程重  
3. **B 站** — 受众匹配，但缺乏稳定的官方 OAuth 投稿 API，暂不建议自动化  

国内抖音 / 小红书 / 快手 / 视频号：**短期只做「生成文案 + 手动发布」**，不要再用浏览器脚本顶风作案。

---

## 合规建议

- **可以**：用 AI 辅助写脚本、生图、配音；**真人**登录创作者后台上传、回复评论  
- **不要**：脚本自动浏览、自动点赞、自动发帖、批量 Cookie 多号操作  
- 小红书等平台已明确：创作过程可用 AI，**发布与互动必须由真人完成**
