# 修复：部署 API 字体错误

## 错误对照

| 报错 | 原因 | 修法 |
|------|------|------|
| `cannot open resource` | 云端没有 Mac 字体路径 | 见下方提示词 A |
| `Read-only file system: '/opt/bytefaas/src/assets'` | **运行时不能**在 `assets/` 建目录/下载字体 | 见下方提示词 B（你当前是这个） |

部署环境（ByteFaaS）中 `/opt/bytefaas/src` **只读**，只能在 **`/tmp`** 写文件，或把字体**打进部署包**。

---

## 【提示词 B · 必读，解决 Read-only file system】

在 [code.coze.cn](https://code.coze.cn/home) 打开「AI新闻60秒」项目，**整段粘贴**：

```text
修复 graphs/nodes/text_overlay_node.py，解决部署 API 报错：
OSError: [Errno 30] Read-only file system: '/opt/bytefaas/src/assets'
发生在 _ensure_font_file() 的 font_dir.mkdir(...)

硬性要求：
1. 禁止在运行时对 /opt/bytefaas/src/assets 或项目 assets 目录执行 mkdir、下载、写入。
2. 字体获取策略（按顺序）：
   a) 若部署包内已有字体：只读打开
      Path(__file__).resolve().parents[2] / "assets" / "fonts" / "NotoSansSC-Regular.otf"
      （你必须把该 .otf 文件真实加入项目 assets/fonts/，随部署打包，不能运行时创建）
   b) Linux 系统字体（只读）：/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf 等
   c) 可写目录 /tmp/coze_fonts/NotoSansSC-Regular.otf — 仅当 a、b 都不存在时，
      用 urllib 下载到 /tmp/coze_fonts/（先 mkdir /tmp/coze_fonts，不要写 src/assets）
   d) 最后 ImageFont.load_default() 并 logging.warning
3. 删除或改写 _ensure_font_file()：不要对 assets 做 mkdir。
4. _load_font(size) 封装上述逻辑，所有 truetype 调用走 _load_font。
5. 若 assets/fonts/ 下没有字体文件，请从 Google Noto 或阿里 CDN 下载 NotoSansSC-Regular.otf
   并提交到项目 assets/fonts/（部署前文件必须已存在）。
6. 修改完成后自动试运行 input=今日AI新闻；通过后明确提示我点击「部署」。

不要依赖 Mac 路径 /System/Library/Fonts/...
```

---

## 【提示词 A · 仅 cannot open resource 时用】

```text
修复 text_overlay_node.py：部署后 ImageFont.truetype 报 cannot open resource。
实现 _load_font(size)，尝试：打包内 assets/fonts/*.otf → Linux 系统字体 → /tmp 下载 → load_default。
禁止运行时写入只读目录。完成后试运行并提示重新部署。
```

---

## 修复后必做（两步缺一不可）

1. 在扣子项目里确认 **assets/fonts/NotoSansSC-Regular.otf 文件真实存在**（文件树能看到，不是空目录）。  
2. **试运行成功** → 右上角 **重新部署** → 本地再跑：

```bash
./scripts/test-coze-vibe.sh
./scripts/run-coze-vibe-workflow.sh "今日AI新闻"
```

不重新部署，API 仍会跑旧代码。

---

## 如何确认字体已打进部署包

在 code.coze.cn 左侧文件树应能看到：

```text
assets/
  fonts/
    NotoSansSC-Regular.otf   ← 必须有实体文件，约 8–16MB
```

若只有 `assets/fonts/` 空目录，AI 常会运行时 mkdir，部署必失败。
