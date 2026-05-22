# 路线 2：扣子编程 AI 工作流（少拖画布）

在 [扣子编程](https://code.coze.cn/home) 用**自然语言**生成「AI新闻60秒」工作流，部署后用本仓库 **`run-coze-vibe-workflow.sh`** 触发 `*.coze.site/run` API。

---

## 操作步骤（约 15–30 分钟）

### 1. 新建 AI 工作流项目

1. 打开 [code.coze.cn/home](https://code.coze.cn/home) → 左侧 **工作流**（带 New 标签）。
2. 在输入框粘贴下方 **【提示词 A · 首次生成】**，单击 **运行**。
3. 等待 AI 生成并自动测试；在画布 **试运行**，输入：`今日AI新闻`。

### 2. 不满意时迭代

在对话区粘贴 **【提示词 B · 修正】**（可多次），或框选某个节点 → **引用** → 描述要改什么。

### 3. 部署

1. 试运行通过后 → **部署** → **开始部署**，等待成功。
2. 右侧 **+** → 标签页 **部署** → **总览** → 某条记录 **查看**。
3. 在 **API 请求示例** 页面：
   - 复制 Curl 里的地址，形如 `https://xxxx.coze.site/run` → 写入 `.env` 的 `COZE_VIBE_RUN_URL`
   - **管理 API Token** → 新建并复制（只显示一次）→ `COZE_VIBE_API_TOKEN`

文档：[通过 API 调用工作流](https://www.coze.cn/api/open/docs/dev_how_to_guides/call_a_deployed_workflow_through_api)

### 4. 接到本仓库（后台自动化）

```bash
cp .env.example .env   # 若已有 .env 只追加下面三行
```

```env
COZE_VIBE_RUN_URL=https://你的子域.coze.site/run
COZE_VIBE_API_TOKEN=部署页生成的_token
COZE_VIBE_INPUT_KEY=input
COZE_WORKFLOW_TOPIC=今日AI新闻
```

试跑（约 5–8 分钟，与试运行耗时接近）：

```bash
chmod +x scripts/run-coze-vibe-workflow.sh
./scripts/run-coze-vibe-workflow.sh "今日AI新闻：Agent、大模型、视频生成"
```

成片在 `output/时间戳.mp4`，元数据在 `logs/last_vibe_run.json`。

**每天定时（macOS）：**

```bash
./scripts/install-daily-vibe-launchd.sh
```

---

## 【提示词 A · 首次生成】（整段复制）

```text
请为我搭建一个「AI 60 秒新闻」工作流，名称 aivideo-v2-slideshow。要求如下：

【业务目标】
- 用户输入 string 类型参数 input，表示今日新闻主题（如「今日AI新闻」）。
- 输出参数 output 为 video 类型，竖屏 9:16，总时长约 50–60 秒。
- 风格：专业 PPT / 幻灯片资讯，深蓝科技风，不要纯文生视频空镜。

【禁止】
- 不要 input 直连 Seedance 文生视频（会只有 5 秒横屏无关画面）。
- 画面内不要生成乱码文字（标题和要点由排版节点叠加）。

【流程必须包含】
1. 开始节点：input (string)
2. 大模型节点：根据 input 生成严格 JSON（不要 markdown 代码块），结构：
   {
     "title": "15字内",
     "slides": [
       {
         "headline": "本页大标题",
         "bullets": ["要点1","要点2","要点3"],
         "narration": "本页口播40-50字中文",
         "image_prompt": "English, vertical 9:16 tech news slide background, no text, professional"
       }
     ]
   }
   slides 数组恰好 5 页：第1页开场，第2-4页各一条 AI 新闻要点，第5页总结。
3. 解析 JSON，得到 slides 数组（可用代码节点或批处理）。
4. 对每一页 slide 循环：
   a. 文生图：用 image_prompt，竖屏 720x1280 或 1080x1920，万相/豆包图像模型。
   b. 画板或图像处理：在图上叠加 title、headline、bullets（深蓝底、白字、圆角条，类似新闻 PPT）。
   c. 语音合成：用 narration 生成中文配音（资讯播音腔）。
   d. 将该页「配图+字幕」与「配音」合成一小段视频，时长=配音时长。
5. 将全部页视频按顺序拼接，页间 0.3 秒淡入淡出，输出最终 MP4。
6. 结束节点：output = 最终视频 URL 或 file。

【模型建议】
- 文案：通义千问 / 豆包大模型
- 图像：万相 2.1 或平台等价文生图
- 语音：豆包语音 / 火山 TTS

【验收标准】
- 试运行 input=「今日AI新闻：Agent、大模型、视频生成」能产出竖屏约 1 分钟视频。
- 每页能看清标题和 3 条要点，有旁白。
- 请自动完成测试；失败则自行修复直到试运行通过。
```

---

## 【提示词 B · 修正】（生成后按需粘贴）

```text
请按以下修改当前工作流，不要重建：

1. 删除任何「input 直接文生视频 / Seedance 纯文生视频」的链路。
2. 确保 5 页幻灯片都走：文生图 → 叠字排版 → TTS → 单页视频 → 拼接。
3. 叠字样式：顶部小字「AI 60s · {title}」，中间大标题 headline，下方 3 条 bullets 圆角条。
4. 最终 output 必须是 video，竖屏 9:16，总时长 45–60 秒。
5. 修完后自动试运行，input 用「今日AI新闻」，把 debug 结果告诉我。
```

---

## 【提示词 C · 第二期可选：图生视频微动】

```text
在保持幻灯片结构和配音不变的前提下，为每一页增加可选分支：
- 文生图完成后，用 Seedance 图生视频（首帧=该图），时长 3 秒，轻微 push-in。
- 若图生视频失败，回退为静态图+配音。
- 仍拼接为 50–60 秒竖屏成片。
```

---

## 常见问题

**Q：API 报错 `cannot open resource` / `401002` 文件读取错误？**  
A：云端找不到中文字体。见 **[coze-vibe-fix-font.md](./coze-vibe-fix-font.md)**，在 code.coze.cn 修 `text_overlay_node.py` 后**重新部署**。

**Q：API 报错 4200？**  
A：未部署或部署失败，回到 code.coze.cn 重新部署。

**Q：output 不是 video？**  
A：用提示词 B 强调结束节点 `output` 类型为 video，并重新部署。

**Q：仍想完全不用 Web？**  
A：首次建流与部署仍需在 code.coze.cn 完成；之后日常可 100% 用 `./scripts/run-coze-vibe-workflow.sh` 自动化。
