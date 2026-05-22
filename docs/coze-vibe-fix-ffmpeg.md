# 修复：部署 API 报错 `No such file or directory: 'ffmpeg'`

## 原因

`video_clip_node.py` 用 `subprocess` 调用系统命令 `ffmpeg`，但扣子部署环境（ByteFaaS）**默认未安装 ffmpeg**，也不在 PATH 里。

---

## 在 code.coze.cn 粘贴此提示词（整段）

```text
修复 graphs/nodes/video_clip_node.py（及 video_concat_node.py 若有），解决部署 API 报错：
FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'

要求：
1. 不要假设系统已安装 ffmpeg；禁止只写 subprocess.run(["ffmpeg", ...])。
2. 任选一种可部署方案（优先 A）：

方案 A（推荐）：项目内捆绑静态 ffmpeg
- 在 assets/bin/ 放置 linux amd64 静态 ffmpeg 可执行文件（或让 AI 下载到该路径，部署前文件必须存在）
- 用 shutil.which 或固定路径解析：
  FFMPEG = Path(__file__).resolve().parents[2] / "assets" / "bin" / "ffmpeg"
  调用时用 [str(FFMPEG), ...] 且 os.chmod(FFMPEG, 0o755) 在构建时完成，运行时只读打开
- 若文件不存在，明确 raise 友好错误「请添加 assets/bin/ffmpeg」

方案 B：纯 Python 合成（无 ffmpeg 依赖）
- 用 moviepy 或 imageio + imageio-ffmpeg（把 ffmpeg 作为 python 包依赖）
- 在 requirements.txt / pyproject 加入 moviepy 或 imageio-ffmpeg，部署时 pip 安装
- create_video_from_image_audio：ImageClip + AudioFileClip → write_videofile

方案 C：改用扣子内置「视频合成」集成/插件节点，删除自定义 subprocess ffmpeg 节点

3. 对 video_concat_node 拼接多段 mp4 同样处理，统一走 _get_ffmpeg_path() 或 moviepy concatenate。
4. 禁止在只读目录 /opt/bytefaas/src 运行时下载二进制；二进制须在部署前打进项目。
5. 修改后自动试运行 input=今日AI新闻，全部节点通过后提示我重新「部署」。
```

---

## 修复后必做

1. 试运行成功（含 video_clip、视频拼接）  
2. **重新部署**  
3. 本地：

```bash
./scripts/run-coze-vibe-workflow.sh "今日AI新闻"
```

---

## 自检清单

| 检查项 | 说明 |
|--------|------|
| `assets/bin/ffmpeg` 存在 | 方案 A 时文件树可见，且为 Linux 可执行 |
| 或 `requirements.txt` 含 moviepy | 方案 B |
| 已重新部署 | 否则 API 仍跑旧代码 |
