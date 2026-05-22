# 修复：API 运行 `cannot open resource`（字体路径）

## 原因

部署环境（Linux）上没有 `text_overlay_node.py` 里写的 `FONT_PATH`，Pillow 加载字体失败。  
试运行若在本地 Mac 可能正常，**部署后 API 必现**。

---

## 在 code.coze.cn 粘贴此提示词（整段）

```text
请修复 graphs/nodes/text_overlay_node.py 的字体问题，解决部署后 API 报错：
OSError: cannot open resource  at ImageFont.truetype(FONT_PATH, 36)

要求：
1. 在项目内新增 assets/fonts/NotoSansSC-Regular.otf（或从网络下载到该路径，部署时随项目打包）。
2. FONT_PATH 改为基于 __file__ 的绝对路径，例如：
   Path(__file__).resolve().parents[2] / "assets" / "fonts" / "NotoSansSC-Regular.otf"
3. 实现 _load_font(size) 函数，按顺序尝试：
   - 项目内 assets/fonts/NotoSansSC-Regular.otf
   - 项目内 assets/fonts/*.ttf / *.otf 任意一个
   - Linux 常见路径：/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf
   - 最后才 ImageFont.load_default()（并打日志 warning）
4. 所有 ImageFont.truetype(FONT_PATH, ...) 改为 _load_font(size)。
5. 若 assets 下无字体文件，在代码里用 urllib 首次运行时下载 Noto Sans SC 到 assets/fonts（仅当文件不存在时）。
6. 修改后自动试运行 input=今日AI新闻，通过后提醒我重新点击「部署」。

不要依赖 Mac 路径如 /System/Library/Fonts/...
```

---

## 修复后你必须做的

1. 试运行成功  
2. 右上角 **重新部署**（不部署 API 仍跑旧代码）  
3. 本地再跑：

```bash
./scripts/test-coze-vibe.sh
./scripts/run-coze-vibe-workflow.sh "今日AI新闻"
```

