# Coze 工作流 V2：幻灯片式 AI 新闻（先出图 → 再合成）

> Coze **没有**对外提供「改画布」的开放 API，需在 [工作流编排页](https://www.coze.cn/work_flow?workflow_id=7641981453953204264&space_id=7522025257893412902) 手动调整，或新建工作流 `aivideo-v2` 后把 `COZE_WORKFLOW_ID` 换成新 ID。  
> 本地已用 `scripts/build_slideshow.py` 实现同一逻辑，可先验证画质再照抄到 Coze。

## 为什么要改

| 旧方案 | 问题 |
|--------|------|
| 一句 `input` → Seedance **文生视频** | 仅 ~5 秒、横屏、画面与新闻无关 |

| 新方案 | 优点 |
|--------|------|
| LLM 分镜 → **文生图** → 幻灯片排版 → TTS → **视频合成** | 像 PPT 资讯，可控、约 60 秒、竖屏 |

图生视频（Seedance **图生视频**）可作为**可选增强**：每页静态幻灯片生成 2–3 秒微动效，再用「视频拼接」节点连成成片；首版建议先用「图片序列 + 配音」保证稳定。

---

## 推荐节点拓扑（复制到 Coze）

```text
[开始] input: string（主题，如「今日AI新闻」）
    ↓
[大模型] 百炼/豆包 — 输出 JSON（5 页分镜）
    提示词见下方「分镜 Prompt」
    ↓
[代码/JSON 解析] 拆成 slides 数组（或用「批处理」节点）
    ↓
[循环] 每一页 slide：
    ├─ [文生图] 万相/豆包图像 — image_prompt，竖屏 720×1280
    ├─ [可选] Seedance 图生视频 — 首帧=上图，时长 3s，微动，9:16
    └─ [画板/图像处理] 叠字：headline + bullets（深蓝科技模板）
    ↓
[循环] 每一页：
    └─ [语音合成] 豆包语音 / 火山 — narration 文本
    ↓
[视频合成/剪映小助手类插件] 按页时长对齐音画，转场「淡入淡出」
    ↓
[结束] output: video
```

### 分镜 Prompt（贴进「大模型」节点）

```text
你是 AI 资讯编导。主题：{{input}}

输出严格 JSON，不要 markdown：
{
  "title": "15字内标题",
  "slides": [
    {
      "headline": "本页大标题",
      "bullets": ["要点1","要点2","要点3"],
      "narration": "本页口播40-50字",
      "image_prompt": "英文，科技新闻幻灯片背景，无文字，竖构图，简洁专业"
    }
  ]
}
slides 恰好 5 页：开场 → 三条新闻 → 总结。
```

### 各节点参数建议

| 节点 | 关键参数 |
|------|----------|
| 文生图 | 模型：万相 2.1 Turbo；比例 **9:16**；禁止画面内文字 |
| Seedance（可选） | 模式：**图生视频**；参考图=上一节点图；720p；3–4 秒；prompt 写 "subtle camera push-in, professional news" |
| 语音合成 | 中文男/女声资讯腔；每段 = 该页 `narration` |
| 视频合成 | 每页时长 = 配音时长；页间 0.3s 交叉淡化 |

### 删除 / 停用

- 原 **单节点文生视频**（input 直连 Seedance）— 易出 5 秒空镜，建议移除。

---

## 与本地脚本对齐

本地命令（不依赖 Coze 画布）：

```bash
pip install -r requirements.txt
python3 scripts/build_slideshow.py "今日AI新闻"
```

成片：`output/slideshow_*.mp4`（1080×1920，约 60 秒）。

Coze 调通 V2 并发布后，只需把 `.env` 里 `COZE_WORKFLOW_ID` 改为新工作流 ID，`run-coze-workflow.sh` 可继续用 API 触发。

---

## 图生视频何时加

1. **第一期**：静态幻灯片 + 配音（最稳，已本地实现）  
2. **第二期**：每页 Seedance 图生视频 3s，再拼接（成本更高，动感更强）
