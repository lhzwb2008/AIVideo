# 火山引擎豆包声音复刻（ICL 2.0）使用说明

本文档说明如何从零完成**字节跳动 / 火山引擎「豆包声音复刻 2.0」**的音色创建与语音合成，供任意调用方**复制配置即可直接调用**。内容仅涉及火山语音服务本身。

---

## 0. 快速开始（复制即用）

当前环境**已完成音色训练**，调用方只需用下方凭证调用合成接口即可出音，无需再走训练流程。

### 0.1 完整环境变量（直接复制到 `.env`）

```bash
# 豆包 / 火山引擎 TTS（声音复刻 ICL 2.0）
VOLCENGINE_TTS_API_KEY=2c49b10e-e086-46d8-b8d8-173fbacff086
VOLCENGINE_TTS_ENDPOINT=https://openspeech.bytedance.com/api/v3/tts/unidirectional
VOLCENGINE_TTS_RESOURCE_ID=seed-icl-2.0
VOLCENGINE_TTS_SPEAKER=S_6uN8A8f22
VOLCENGINE_TTS_MODEL=seed-tts-2.0-standard
VOLCENGINE_TTS_UID=aivideo
VOLCENGINE_TTS_FORMAT=mp3
VOLCENGINE_TTS_SAMPLE_RATE=24000
VOLCENGINE_TTS_RATE=1.0
VOLCENGINE_TTS_SPEECH_RATE=0
VOLCENGINE_TTS_LOUDNESS_RATE=0
VOLCENGINE_TTS_ATEMPO=1.18
```

### 0.2 凭证一览

| 配置项 | 真实值 | 用途 |
|--------|--------|------|
| API Key | `2c49b10e-e086-46d8-b8d8-173fbacff086` | V3 语音合成鉴权（Header `X-Api-Key`） |
| 克隆音色 Speaker ID | `S_6uN8A8f22` | 已训练好的克隆音色，合成时填入 `req_params.speaker` |
| Resource ID | `seed-icl-2.0` | 克隆音色固定值，Header `X-Api-Resource-Id` |
| 合成模型 | `seed-tts-2.0-standard` | 请求体 `req_params.model` |
| 合成端点 | `https://openspeech.bytedance.com/api/v3/tts/unidirectional` | HTTP POST |
| 用户标识 uid | `aivideo` | 请求体 `user.uid`，可改为任意字符串 |
| 音频格式 | `mp3` / 采样率 `24000` | `req_params.audio_params` |

> **说明**：若需**重新上传参考音频、训练新音色**，还需火山控制台的应用 **App ID** 和 **Access Token**（训练接口专用）。当前凭证包已包含可用克隆音色，**直接合成即可**。

### 0.3 30 秒验证（Python，无需安装依赖）

保存为 `test_tts.py`，直接运行 `python3 test_tts.py`：

```python
#!/usr/bin/env python3
import base64, json, uuid, urllib.request

API_KEY = "2c49b10e-e086-46d8-b8d8-173fbacff086"
SPEAKER = "S_6uN8A8f22"

body = {
    "user": {"uid": "aivideo"},
    "namespace": "BidirectionalTTS",
    "req_params": {
        "text": "你好，这是豆包声音复刻测试。",
        "speaker": SPEAKER,
        "model": "seed-tts-2.0-standard",
        "audio_params": {"format": "mp3", "sample_rate": 24000, "speech_rate": 0, "loudness_rate": 0},
        "additions": json.dumps({"model_type": 4}),
    },
}
req = urllib.request.Request(
    "https://openspeech.bytedance.com/api/v3/tts/unidirectional",
    data=json.dumps(body, ensure_ascii=False).encode(),
    headers={
        "Content-Type": "application/json",
        "X-Api-Key": API_KEY,
        "X-Api-Resource-Id": "seed-icl-2.0",
        "X-Api-Request-Id": str(uuid.uuid4()),
    },
    method="POST",
)
with urllib.request.urlopen(req, timeout=120) as resp:
    raw = resp.read().decode()

audio = bytearray()
for line in raw.splitlines():
    if not line.strip():
        continue
    chunk = json.loads(line)
    if chunk.get("data"):
        audio.extend(base64.b64decode(chunk["data"]))

open("test_output.mp3", "wb").write(bytes(audio))
print(f"OK → test_output.mp3 ({len(audio)//1024} KB)")
```

### 0.4 完整合成请求（cURL，密钥已填好）

```bash
curl -sS -X POST "https://openspeech.bytedance.com/api/v3/tts/unidirectional" \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: 2c49b10e-e086-46d8-b8d8-173fbacff086" \
  -H "X-Api-Resource-Id: seed-icl-2.0" \
  -H "X-Api-Request-Id: $(uuidgen)" \
  -d '{
    "user": {"uid": "aivideo"},
    "namespace": "BidirectionalTTS",
    "req_params": {
      "text": "你好，这是克隆音色合成测试。",
      "speaker": "S_6uN8A8f22",
      "model": "seed-tts-2.0-standard",
      "audio_params": {
        "format": "mp3",
        "sample_rate": 24000,
        "speech_rate": 0,
        "loudness_rate": 0
      },
      "additions": "{\"model_type\":4}"
    }
  }'
```

响应为多行 NDJSON，每行一个 JSON；`code=0` 且带 `data` 的行是 Base64 音频片段，拼接后即为 mp3。

---

## 1. 能力概览

| 阶段 | 做什么 | 主要接口 |
|------|--------|----------|
| 控制台准备 | 开通服务、购买/领取音色槽位、获取凭证 | 火山引擎控制台 |
| 训练音色 | 上传参考音频，生成专属 `speaker_id`（形如 `S_xxxxxxxxx`） | `POST /api/v1/mega_tts/audio/upload` |
| 查询状态 | 确认训练是否成功 | `POST /api/v1/mega_tts/status` |
| 语音合成 | 用克隆音色把文本转成音频 | `POST /api/v3/tts/unidirectional`（推荐） |

**推荐版本**：声音复刻 **ICL 2.0**（训练时 `model_type=4`，合成时 `X-Api-Resource-Id=seed-icl-2.0`）。

---

## 2. 官方文档与控制台链接

| 资源 | 链接 |
|------|------|
| 火山引擎控制台 | https://console.volcengine.com/ |
| 声音复刻下单及使用指南 | https://www.volcengine.com/docs/6561/1167802 |
| 声音复刻 API（上传 / 查询状态） | https://www.volcengine.com/docs/6561/1305191 |
| 大模型语音合成 V3（HTTP Chunked 单向流式） | https://www.volcengine.com/docs/6561/1598757 |
| 大模型语音合成 V3（WebSocket 双向流式） | https://www.volcengine.com/docs/6561/1329505 |
| 产品计费说明 | https://www.volcengine.com/docs/6561/1099324 |
| API Key 管理（新版控制台） | 控制台 → 豆包语音 → API Key 管理 |

---

## 3. 凭证与配置说明

### 3.1 当前可用凭证（合成，已验证可用）

| 变量名 | 真实值 |
|--------|--------|
| `VOLCENGINE_TTS_API_KEY` | `2c49b10e-e086-46d8-b8d8-173fbacff086` |
| `VOLCENGINE_TTS_SPEAKER` | `S_6uN8A8f22` |
| `VOLCENGINE_TTS_RESOURCE_ID` | `seed-icl-2.0` |
| `VOLCENGINE_TTS_MODEL` | `seed-tts-2.0-standard` |
| `VOLCENGINE_TTS_ENDPOINT` | `https://openspeech.bytedance.com/api/v3/tts/unidirectional` |
| `VOLCENGINE_TTS_UID` | `aivideo` |
| `VOLCENGINE_TTS_FORMAT` | `mp3` |
| `VOLCENGINE_TTS_SAMPLE_RATE` | `24000` |
| `VOLCENGINE_TTS_SPEECH_RATE` | `0` |
| `VOLCENGINE_TTS_LOUDNESS_RATE` | `0` |
| `VOLCENGINE_TTS_RATE` | `1.0` |
| `VOLCENGINE_TTS_ATEMPO` | `1.18` |

V3 合成接口 Header（直接照抄）：

```http
Content-Type: application/json
X-Api-Key: 2c49b10e-e086-46d8-b8d8-173fbacff086
X-Api-Resource-Id: seed-icl-2.0
X-Api-Request-Id: <UUID，每次请求换一个>
```

### 3.2 训练接口凭证（仅重新克隆音色时需要）

上传 / 查询训练状态接口使用 **Bearer Token** 鉴权，需从[火山控制台](https://console.volcengine.com/) → 豆包语音 → 应用详情获取：

| 变量名 | 说明 |
|--------|------|
| `VOLCENGINE_APP_ID` | 应用 App ID（纯数字） |
| `VOLCENGINE_ACCESS_TOKEN` | 应用 Access Token |

```http
Authorization: Bearer; <Access Token>
Resource-Id: seed-icl-2.0
Content-Type: application/json
```

> `Bearer` 与 Token 之间是**分号 + 空格**（`Bearer; xxx`），不是 `Bearer xxx`。  
> 当前 `S_6uN8A8f22` 已训练完成，**一般接入方只需 3.1 的合成凭证**。

---

## 4. 从零到一：完整流程

```
┌─────────────────┐
│ 1. 控制台开通服务 │  开通「声音复刻 2.0」+ 获取/购买 speaker 槽位
└────────┬────────┘
         ▼
┌─────────────────┐
│ 2. 获取 speaker_id│  控制台创建应用后分配，形如 S_xxxxxxxxx
└────────┬────────┘
         ▼
┌─────────────────┐
│ 3. 准备参考音频   │  10–30 秒、单人清晰人声、无背景音乐
└────────┬────────┘
         ▼
┌─────────────────┐
│ 4. 上传训练       │  POST .../mega_tts/audio/upload
└────────┬────────┘
         ▼
┌─────────────────┐
│ 5. 轮询训练状态   │  POST .../mega_tts/status → status=2 或 4
└────────┬────────┘
         ▼
┌─────────────────┐
│ 6. 语音合成       │  POST .../tts/unidirectional
└─────────────────┘
```

### 步骤 1：控制台开通服务

1. 注册并登录 [火山引擎控制台](https://console.volcengine.com/)。
2. 进入 **豆包语音** → **开通管理**，勾选并开通：
   - **豆包声音复刻模型 2.0**（或「声音复刻大模型」）
   - 如需后付费音色，另开通 **音色服务**
3. **创建应用**，勾选声音复刻相关能力。
4. 在 **开通管理 → 快速购买** 购买预付费音色槽位，或使用控制台赠送的免费槽位（具体以控制台为准）。
5. 记录应用详情页中的 **App ID**、**Access Token**、**Speaker ID**。
6. 在 **API Key 管理** 中创建 API Key（用于 V3 合成）。

详细说明见官方文档：[声音复刻下单及使用指南](https://www.volcengine.com/docs/6561/1167802)。

### 步骤 2：准备参考音频

| 要求 | 说明 |
|------|------|
| 时长 | 建议 **10–30 秒** |
| 内容 | **仅一人说话**，无背景音乐、无多人重叠 |
| 质量 | 尽量干净、信噪比高；噪声大时可开启降噪（见 upload 参数） |
| 格式 | wav、mp3、ogg、m4a、aac、pcm（pcm 仅支持 24k 单声道） |
| 大小 | 单文件最大 **10 MB** |

可选：在 upload 时附带 `text` 字段（参考文本），服务会比对音频与文本；差异过大会返回 `1109 WERError`。

### 步骤 3：上传音频训练音色

**接口**

```
POST https://openspeech.bytedance.com/api/v1/mega_tts/audio/upload
```

**请求 Header**

```http
Authorization: Bearer; <从控制台获取的 Access Token>
Resource-Id: seed-icl-2.0
Content-Type: application/json
```

**请求 Body**

```json
{
  "appid": "<从控制台获取的 App ID>",
  "speaker_id": "S_6uN8A8f22",
  "audios": [
    {
      "audio_bytes": "<Base64 编码的音频二进制>",
      "audio_format": "wav"
    }
  ],
  "source": 2,
  "language": 0,
  "model_type": 4
}
```

**关键参数**

| 字段 | 必填 | 说明 |
|------|------|------|
| `appid` | 是 | 控制台 App ID |
| `speaker_id` | 是 | 控制台预先分配的 Speaker ID（`S_xxx`） |
| `audios[].audio_bytes` | 是 | Base64 编码音频 |
| `audios[].audio_format` | 建议 | 音频格式；pcm、m4a 必传 |
| `source` | 是 | 固定值 `2` |
| `language` | 是 | `0`=中文（默认），`1`=英文（ICL 2.0 仅支持中英文） |
| `model_type` | 是 | ICL 2.0 使用 **`4`**（ICL V2）；`5` 为 ICL V3 |

**成功响应示例**

```json
{
  "BaseResp": {
    "StatusCode": 0,
    "StatusMessage": ""
  },
  "speaker_id": "S_xxxxxxxxx"
}
```

**常见错误码**

| 码 | 含义 |
|----|------|
| 1104 | 声纹与名人过于相似 |
| 1109 | 音频与参考文本字错率过高 |
| 1112 | 信噪比过低 |
| 1122 | 未检测到人声 |
| 1123 | 同一音色上传次数已达上限（通常 10 次） |

### 步骤 4：查询训练状态

**接口**

```
POST https://openspeech.bytedance.com/api/v1/mega_tts/status
```

**请求 Header / Body**

```http
Authorization: Bearer; <从控制台获取的 Access Token>
Resource-Id: seed-icl-2.0
Content-Type: application/json
```

```json
{
  "appid": "<从控制台获取的 App ID>",
  "speaker_id": "S_6uN8A8f22"
}
```

**status 枚举**

| 值 | 含义 |
|----|------|
| 0 | NotFound |
| 1 | Training（训练中） |
| 2 | Success（可合成） |
| 3 | Failed |
| 4 | Active（可合成） |

当 `status` 为 **2 或 4** 时即可调用 TTS。训练成功后响应可能包含 `demo_audio`（试听链接，约 1 小时有效）。

**轮询建议**：训练通常需要数十秒到数分钟，建议每 3–5 秒查询一次，最多等待 5–10 分钟。

### 步骤 5：使用克隆音色合成语音

**接口（推荐）**

```
POST https://openspeech.bytedance.com/api/v3/tts/unidirectional
```

**请求 Header**

```http
Content-Type: application/json
X-Api-Key: 2c49b10e-e086-46d8-b8d8-173fbacff086
X-Api-Resource-Id: seed-icl-2.0
X-Api-Request-Id: 67ee89ba-7050-4c04-a3d7-ac61a63499b3
```

**请求 Body**

```json
{
  "user": {
    "uid": "aivideo"
  },
  "namespace": "BidirectionalTTS",
  "req_params": {
    "text": "你好，这是一段使用克隆音色合成的测试语音。",
    "speaker": "S_6uN8A8f22",
    "model": "seed-tts-2.0-standard",
    "audio_params": {
      "format": "mp3",
      "sample_rate": 24000,
      "speech_rate": 0,
      "loudness_rate": 0
    },
    "additions": "{\"model_type\":4}"
  }
}
```

**音色类型与 Resource ID 对照**

| 音色类型 | Speaker ID 特征 | X-Api-Resource-Id | req_params.model | additions.model_type |
|----------|-----------------|-------------------|------------------|----------------------|
| 克隆音色 ICL 2.0 | `S_xxxxxxxxx` | `seed-icl-2.0` | `seed-tts-2.0-standard`（推荐）或 `seed-tts-2.0-expressive` | `4`（建议显式传入） |
| 官方 2.0 音色 | `*_uranus_bigtts` 等 | `seed-tts-2.0` | 可不传 | 不需要 |
| 官方 1.0 音色 | `*_mars_bigtts`、`ICL_*` 等 | `seed-tts-1.0` | 可不传 | 不需要 |

> **重要**：克隆音色（`S_` 开头）必须使用 `seed-icl-2.0`，且建议在 `additions` 中设置 `"model_type": 4`。`additions` 是 **JSON 字符串**，不是嵌套对象。

**响应格式（NDJSON 流式）**

服务端按行返回 JSON，每行一条消息：

```json
{"code": 0, "message": "", "data": "<Base64 音频片段>"}
{"code": 0, "message": "", "data": "<Base64 音频片段>"}
{"code": 20000000, "message": "ok", "data": null, "usage": {"text_words": 10}}
```

处理方式：

1. 逐行读取响应体；
2. 对 `code` 为 `0` 且 `data` 非空的行，Base64 解码后拼接到音频字节流；
3. 收到 `code: 20000000` 表示合成结束；
4. 将拼接后的字节写入 `.mp3` 文件即可播放。

**合成参数说明**

| 字段 | 范围 / 取值 | 说明 |
|------|-------------|------|
| `speech_rate` | [-50, 100] | 100 = 2 倍速，-50 = 0.5 倍速，0 = 正常 |
| `loudness_rate` | [-50, 100] | 100 = 2 倍音量 |
| `format` | mp3 / ogg_opus / pcm | 推荐 mp3 |
| `sample_rate` | 8000–48000 | 推荐 24000 |

---

## 5. 可直接运行的示例代码

### 5.1 Python：语音合成（推荐，凭证已内置）

依赖：Python 3.9+，标准库即可。保存为 `synthesize.py`，运行：

```bash
python3 synthesize.py "要合成的文本" output.mp3
```

```python
#!/usr/bin/env python3
"""豆包声音复刻 ICL 2.0 — 语音合成（凭证已填好，直接运行）"""

import base64
import json
import sys
import uuid
import urllib.request
import urllib.error

# ── 当前可用凭证 ──
API_KEY = "2c49b10e-e086-46d8-b8d8-173fbacff086"
SPEAKER = "S_6uN8A8f22"
RESOURCE_ID = "seed-icl-2.0"
MODEL = "seed-tts-2.0-standard"
UID = "aivideo"
ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"


def synthesize(text: str, out_path: str) -> None:
    body = {
        "user": {"uid": UID},
        "namespace": "BidirectionalTTS",
        "req_params": {
            "text": text,
            "speaker": SPEAKER,
            "model": MODEL,
            "audio_params": {
                "format": "mp3",
                "sample_rate": 24000,
                "speech_rate": 0,
                "loudness_rate": 0,
            },
            "additions": json.dumps({"model_type": 4}),
        },
    }
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": API_KEY,
            "X-Api-Resource-Id": RESOURCE_ID,
            "X-Api-Request-Id": str(uuid.uuid4()),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as e:
        logid = e.headers.get("X-Tt-Logid", "")
        raise RuntimeError(f"HTTP {e.code} logid={logid}: {e.read().decode()[:500]}") from e

    audio = bytearray()
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        chunk = json.loads(line)
        code = chunk.get("code")
        if code not in (0, 20000000, None):
            raise RuntimeError(f"TTS 失败: {chunk}")
        if chunk.get("data"):
            audio.extend(base64.b64decode(chunk["data"]))

    if not audio:
        raise RuntimeError("未收到音频数据")

    with open(out_path, "wb") as f:
        f.write(bytes(audio))
    print(f"saved {out_path} ({len(audio)//1024} KB)")


if __name__ == "__main__":
    text = sys.argv[1] if len(sys.argv) > 1 else "你好，克隆音色测试成功。"
    out = sys.argv[2] if len(sys.argv) > 2 else "output.mp3"
    synthesize(text, out)
```

### 5.2 Python：上传训练 + 查询状态 + 合成（需补充控制台 App ID / Token）

若需**重新上传参考音频**覆盖训练 `S_6uN8A8f22`，还需在下方填入控制台的应用凭证：

```python
#!/usr/bin/env python3
"""火山引擎豆包声音复刻 2.0 — 完整流程（训练 + 合成）"""

import base64
import json
import time
import uuid
import urllib.request
import urllib.error

# ── 合成凭证（已填好）──
API_KEY = "2c49b10e-e086-46d8-b8d8-173fbacff086"
SPEAKER_ID = "S_6uN8A8f22"

# ── 训练凭证（从火山控制台应用详情页获取后填入）──
APP_ID = ""           # 例: "1234567890"
ACCESS_TOKEN = ""     # 例: "your_access_token_here"

BASE = "https://openspeech.bytedance.com"


def upload_voice(audio_path: str) -> None:
    if not APP_ID or not ACCESS_TOKEN:
        raise RuntimeError("请先填入 APP_ID 和 ACCESS_TOKEN（火山控制台 → 应用详情）")
    with open(audio_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    ext = audio_path.rsplit(".", 1)[-1].lower()
    body = {
        "appid": APP_ID,
        "speaker_id": SPEAKER_ID,
        "audios": [{"audio_bytes": b64, "audio_format": ext}],
        "source": 2,
        "language": 0,
        "model_type": 4,
    }
    req = urllib.request.Request(
        f"{BASE}/api/v1/mega_tts/audio/upload",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer; {ACCESS_TOKEN}",
            "Resource-Id": "seed-icl-2.0",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        print("upload:", resp.read().decode())


def wait_training(timeout_s: int = 300) -> None:
    body = {"appid": APP_ID, "speaker_id": SPEAKER_ID}
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        req = urllib.request.Request(
            f"{BASE}/api/v1/mega_tts/status",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer; {ACCESS_TOKEN}",
                "Resource-Id": "seed-icl-2.0",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        status = data.get("status")
        print(f"status={status}", data)
        if status in (2, 4):
            return
        if status == 3:
            raise RuntimeError("训练失败")
        time.sleep(5)
    raise TimeoutError("训练超时")


def synthesize(text: str, out_path: str) -> None:
    body = {
        "user": {"uid": "aivideo"},
        "namespace": "BidirectionalTTS",
        "req_params": {
            "text": text,
            "speaker": SPEAKER_ID,
            "model": "seed-tts-2.0-standard",
            "audio_params": {
                "format": "mp3",
                "sample_rate": 24000,
                "speech_rate": 0,
                "loudness_rate": 0,
            },
            "additions": json.dumps({"model_type": 4}),
        },
    }
    req = urllib.request.Request(
        f"{BASE}/api/v3/tts/unidirectional",
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": API_KEY,
            "X-Api-Resource-Id": "seed-icl-2.0",
            "X-Api-Request-Id": str(uuid.uuid4()),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode()

    audio = bytearray()
    for line in raw.splitlines():
        if not line.strip():
            continue
        chunk = json.loads(line)
        if chunk.get("data"):
            audio.extend(base64.b64decode(chunk["data"]))
    open(out_path, "wb").write(bytes(audio))
    print(f"saved {out_path} ({len(audio)//1024} KB)")
```

### 5.3 cURL：语音合成（密钥已填好）

```bash
curl -sS -X POST "https://openspeech.bytedance.com/api/v3/tts/unidirectional" \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: 2c49b10e-e086-46d8-b8d8-173fbacff086" \
  -H "X-Api-Resource-Id: seed-icl-2.0" \
  -H "X-Api-Request-Id: $(uuidgen)" \
  -d '{
    "user": {"uid": "aivideo"},
    "namespace": "BidirectionalTTS",
    "req_params": {
      "text": "你好，这是克隆音色测试。",
      "speaker": "S_6uN8A8f22",
      "model": "seed-tts-2.0-standard",
      "audio_params": {"format": "mp3", "sample_rate": 24000, "speech_rate": 0, "loudness_rate": 0},
      "additions": "{\"model_type\":4}"
    }
  }'
```

响应为多行 JSON，需自行解析 Base64 并拼接为 mp3 文件。

### 5.4 cURL：上传训练（需自行填入 App ID / Access Token）

```bash
AUDIO_B64=$(base64 -i reference.wav)
curl -sS -X POST "https://openspeech.bytedance.com/api/v1/mega_tts/audio/upload" \
  -H "Authorization: Bearer; <控制台 Access Token>" \
  -H "Resource-Id: seed-icl-2.0" \
  -H "Content-Type: application/json" \
  -d "{
    \"appid\": \"<控制台 App ID>\",
    \"speaker_id\": \"S_6uN8A8f22\",
    \"audios\": [{\"audio_bytes\": \"${AUDIO_B64}\", \"audio_format\": \"wav\"}],
    \"source\": 2,
    \"language\": 0,
    \"model_type\": 4
  }"
```

---

## 6. 进阶：情感与自然语言控制（ICL 2.0）

ICL 2.0 支持通过 `additions.context_texts` 用自然语言描述语气，例如：

```json
"additions": "{\"model_type\":4,\"context_texts\":[\"用温柔、自然的语气\"]}"
```

`seed-tts-2.0-expressive` 模型表现力更强，但可能存在效果波动；默认 `seed-tts-2.0-standard` 延时更低、更稳定。

---

## 7. 常见问题

### Q1：`55000000: resource ID is mismatched`

`X-Api-Resource-Id` 与 `speaker` 类型不匹配。克隆音色（`S_` 开头）必须用 `seed-icl-2.0`。

### Q2：合成没有声音 / data 为空

- 确认训练 `status` 为 2 或 4；
- 确认 `additions` 中 `model_type` 与训练版本一致（ICL 2.0 为 4）；
- 检查响应中是否有非 0 的 `code`。

### Q3：Bearer Token 鉴权失败

确认格式为 `Bearer; <token>`（分号分隔），且 Token 未过期。

### Q4：一个 speaker_id 可以训练几次？

同一 `speaker_id` 通常支持 **10 次**上传（以官方限制为准）。重新训练会覆盖原有音色效果。

### Q5：官方音色 vs 克隆音色

| 类型 | Speaker 示例 | Resource ID |
|------|--------------|-------------|
| 克隆 ICL 2.0 | `S_abc123` | `seed-icl-2.0` |
| 官方 TTS 2.0 | `zh_male_dayi_uranus_bigtts` | `seed-tts-2.0` |
| 官方 TTS 1.0 | `zh_male_M392_conversation_wvae_bigtts` | `seed-tts-1.0` |

---

## 8. 上线检查清单

- [x] 克隆音色 `S_6uN8A8f22` 已训练完成，可直接合成
- [x] API Key `2c49b10e-e086-46d8-b8d8-173fbacff086` 已验证可用
- [ ] 合成使用 `X-Api-Resource-Id=seed-icl-2.0` + `model=seed-tts-2.0-standard`
- [ ] `additions` 中包含 `"model_type": 4`
- [ ] 已实现 NDJSON 流式响应的 Base64 拼接逻辑
- [ ] 若需重新训练：补充控制台 App ID / Access Token 后再走 upload → status 流程

---

## 9. 版本说明

| 训练 model_type | 效果 | 合成 Resource ID |
|-----------------|------|------------------|
| 4 | 声音复刻 ICL V2（**推荐**） | `seed-icl-2.0` |
| 5 | 声音复刻 ICL V3（更新，2026.03 起） | `seed-icl-2.0` |
| 1 | 声音复刻 ICL 1.0 | `seed-icl-1.0` |

新接入建议优先使用 **ICL 2.0（model_type=4）+ V3 HTTP 单向流式接口**。

---

*文档基于火山引擎公开 API 文档整理，接口细节以官方最新文档为准。*
