# 三个 ComfyUI shim:空白新机器部署手册

把 `image` / `qwentts` / `music` 三个 shim 从零部署到一台新机器。

> **这份文档管什么**:三个 shim 服务本身——依赖、启动、端口、systemd 托管、健康检查、接回 shanhai。这一层可 100% 复现。
>
> **不管什么**:ComfyUI 本体、自定义节点、模型权重的安装。那些是**外部依赖、且不在本仓库**,本文只给出「你必须自备什么」的精确清单(见 §2),给不出保证跑通的安装步骤。
>
> 相关文档:shim 源码存档与设计说明见 [`../scripts/dgx-shims/README.md`](../scripts/dgx-shims/README.md);参考机(DGX)的历史变更见 [`deploy-dgx.md`](deploy-dgx.md);日常运维速查见 [`ops-dgx.md`](ops-dgx.md)。

---

## 1. 架构:shim 是什么

三个 shim 都是**极薄的 FastAPI 转发层**,自己不做任何生成计算。它们把 shanhai 发来的 OpenAI 兼容请求,翻译成 ComfyUI 的排队协议,再把结果转回 OpenAI 风格响应。真正算力在 ComfyUI。

```
shanhai (S3/S4/S5)
   │  OpenAI 兼容 HTTP
   ▼
┌──────────────┬──────────────┬──────────────┐
│ image-shim   │ qwentts-shim │ music-shim   │   ← 本文部署的三个进程
│ :8091        │ :8090        │ :8092        │
└──────┬───────┴──────┬───────┴──────┬───────┘
       │  ComfyUI HTTP + WebSocket 排队协议
       ▼              ▼              ▼
              ComfyUI  127.0.0.1:8188            ← 前置依赖,本文不装(见 §2)
       (自定义节点 + 模型权重 + 6 个工作流 JSON)
```

| shim | 端口 | 对 shanhai 暴露的路由 | 与 ComfyUI 的通信 |
|---|---|---|---|
| image | 8091 | `POST /v1/images/generations`、`POST /v1/images/edits` | 纯 HTTP 轮询 `/history` |
| qwentts | 8090 | `POST /v1/audio/speech`、`POST /v1/voices/clone`、`GET /v1/models` | WebSocket(`/ws`)监听完成事件 |
| music | 8092 | `POST /v1/audio/music` | WebSocket(`/ws`)监听完成事件 |

三者都另有 `GET /health`(注意:在**根路径**,不带 `/v1` 前缀)。

---

## 2. 前置:ComfyUI 那一半(你必须自备)

**没有这一层,三个 shim 全部 500 / 502。** 本文不覆盖它的安装,但把「shim 依赖它提供什么」逐条列清,方便你去参考机的 ComfyUI 里索取、或从上游自行搭建。

### 2.1 一个能跑的 ComfyUI

- 监听 `127.0.0.1:8188`,HTTP 与 WebSocket(`/ws`)都通。
- 自检:`curl -s http://127.0.0.1:8188/system_stats`(三个 shim 的 `/health` 探的就是它)。

### 2.2 工作流模板 JSON(6 个 + 1 个特殊)

shim 每次请求都**现读**这些 JSON,并按写死的节点号往里塞参数。**模板不在本仓库**,必须单独拷过来。节点号与模板一一对应,换了模板就要同步改 shim 源码里的节点常量。

| 用途 | 模板文件 | shim 注入的节点 | 依赖的自定义节点 / 模型 |
|---|---|---|---|
| 文生图(S3 三视图) | `Text2IMGKrea2_api.json` | `51`=prompt、`49`=aspect_ratio(**无 LoRA 节点**) | AspectRatio(ResolutionSelector)+ 底模 |
| 单参考图编辑 | `image_edit_workflow.json` | 图`41`、prompt`68`、ratio`126`、LoRA`133` | LoraLoaderModelOnly + LoRA 权重 |
| 双参考图融合 | `image_blend_workflow.json` | 图`41`+`79`、LoRA`133` | 同上 |
| 三参考图融合 | `image_triple_blend_workflow.json` | 图`41`+`79`+`133`、LoRA`135`(**不是 133**) | 同上 |
| 语音设计(内置音色) | `VoiceDesign-QwenTTS.json` | text`75`、声音描述`76`、语速`73` | Qwen3TTSVoiceDesign 节点 + Qwen3-TTS 模型 |
| 音色克隆 | `VoiceClone-QwenTTS.json` ⚠️ | audio`151`、text`153`(无语速节点) | LoadAudio + Qwen3-TTS |
| 背景音乐(S5 BGM) | `MusicCreation-ACESTEP1.5XL_api.json` | 歌词`40`、风格`41`、时长`43`、bpm`45`、encode`36` | TextEncodeAceStepAudio1.5 + ACE-Step v1.5xl 模型 |

⚠️ **`VoiceClone-QwenTTS.json` 连存档副本都没有**——它是 qwentts-shim 唯一一个「拷副本、放脚本同目录」的模板(其余都是引用外部目录)。仓库里三个 `*.main.py` 存档**不含任何工作流 JSON**。新机器上这 7 个文件都要你自备。若暂时不需要音色克隆,可以先不放它,`/v1/audio/speech` 的内置音色路径不受影响,只有 `clone:` 前缀的请求会 500。

### 2.3 模型权重与自定义节点

上表右列点名的东西都得在 ComfyUI 里就位——GB 级、机器专属:

- **图像**:底模(Qwen/Krea 系)+ 三个 LoRA safetensors,短名到文件名的映射写死在 image-shim 里:
  - `real_ani_qwen` → `Real_Ani-Qwen_000001250.safetensors`
  - `figurine_qwen` → `figurine_qwen.safetensors`
  - `bjd.7arl` → `bjdE5A883E5A883V2004.7ARL.safetensors`
- **语音**:Qwen3-TTS(VoiceDesign / VoiceClone)+ `Qwen3TTSVoiceDesign` 自定义节点。
- **音乐**:ACE-Step v1.5xl + `TextEncodeAceStepAudio1.5` 自定义节点。

这些的具体来源(HuggingFace 仓库、自定义节点 git 地址)**源码里给不出**,只能从参考机的 ComfyUI 安装里索取或按模型名自行去上游找。**这是本文档的硬边界。**

---

## 3. shim 层依赖(这一层能写全)

### 3.1 Python(用 uv)

每个 shim 都是一个自包含的 uv 项目:仓库里 `scripts/dgx-shims/<shim>/` 各含一个
`main.py` 和一个 `pyproject.toml`。部署时把整个目录拷到 home,`cd` 进去 `uv sync`
就会建好 `.venv` 并装好依赖(§4 逐个给命令)。需要 Python ≥ 3.10 与 `uv`。

三者依赖略有差别(已按各自实际 import 精确声明,不用背):

| 依赖 | image | qwentts | music | 为什么 |
|---|:-:|:-:|:-:|---|
| `fastapi` / `uvicorn[standard]` / `httpx` | ✓ | ✓ | ✓ | 三者的骨架 |
| `websockets` | | ✓ | ✓ | 作为客户端连 ComfyUI 的 `/ws`;image 走 HTTP 轮询,用不到 |
| `python-multipart` | ✓ | ✓ | | 收 multipart 上传(image `/images/edits`、qwentts `/voices/clone`);music 只收 JSON |

> `pyproject.toml` 里写了 `[tool.uv] package = false`——因为 shim 是散装脚本、不是可
> 安装的包,这行让 `uv sync` 只装依赖、不去构建本项目。别删。

> 已经手动建过 venv(比如某次 `uv venv` 留下的空 `.venv`)也没关系:在项目目录里
> `uv sync` 会直接复用/补齐那个 `.venv`。

### 3.2 ffmpeg(带 libmp3lame)

- **qwentts**:仅音色克隆的调速用(`atempo`);内置音色不经过它。
- **music**:每次都用(把 ComfyUI 输出统一转 mp3),**硬依赖**。
- **image**:不需要 ffmpeg。

必须是**带 libmp3lame 的完整构建**:

```bash
ffmpeg -encoders 2>/dev/null | grep libmp3lame     # 有输出才算过关
```

> 参考机上的坑:系统自带的 ffmpeg 是残缺构建(无 libmp3lame),两个音频 shim 转 mp3 直接失败,只能另指一个完整版。新机器若 `which ffmpeg` 就是完整版,可不管;否则用下面的 `FFMPEG_BIN` 环境变量指到完整版的绝对路径。

---

## 4. 逐个部署

下面用占位符:`<COMFYUI_ROOT>` = 你存放 §2.2 工作流 JSON 的目录。拷贝命令从**仓库根目录**执行;每个 shim 拷成 home 下一个独立目录,各自 `uv sync` 建 `.venv`。

### 4.1 image-shim

```bash
cp -r scripts/dgx-shims/image-shim ~/image-shim   # 得到 ~/image-shim/{main.py, pyproject.toml}
cd ~/image-shim && uv sync                        # 建 .venv、装依赖
```

⚠️ **image-shim 的 ComfyUI 地址和模板目录是硬编码常量,必须改源码**(它是三个里唯一不认环境变量的)。编辑 `~/image-shim/main.py` 顶部:

```python
COMFYUI_SERVER = "http://127.0.0.1:8188"                 # 你的 ComfyUI 地址
COMFYUI_ROOT   = Path("<COMFYUI_ROOT>")                  # 改成你放 4 个图像工作流 JSON 的目录
```

启动 + 冒烟(在 `~/image-shim` 下):

```bash
uv run uvicorn main:app --host 127.0.0.1 --port 8091
curl -s http://127.0.0.1:8091/health                     # {"ok": true} 才算 ComfyUI 也通
```

### 4.2 qwentts-shim

```bash
cp -r scripts/dgx-shims/qwentts-shim ~/qwentts-shim
cd ~/qwentts-shim && uv sync
# 若要音色克隆,把自备的 VoiceClone-QwenTTS.json 也放进 ~/qwentts-shim/
```

qwentts 认环境变量,**不改源码**,靠 systemd 或 shell 注入(见 §5):

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `COMFYUI_HTTP` | `http://127.0.0.1:8188` | ComfyUI HTTP |
| `COMFYUI_WS` | `ws://127.0.0.1:8188/ws` | ComfyUI WebSocket |
| `WORKFLOW_JSON_PATH` | 参考机专属路径 | **必设**:指到你的 `VoiceDesign-QwenTTS.json` |
| `CLONE_WORKFLOW_JSON_PATH` | 脚本同目录的 `VoiceClone-QwenTTS.json` | 克隆模板;放同目录就不用设 |
| `FFMPEG_BIN` | `ffmpeg` | 完整版 ffmpeg 不在 PATH 时指绝对路径 |
| `QWENTTS_SHIM_POLL_TIMEOUT_S` | `180` | 合成超时 |

```bash
WORKFLOW_JSON_PATH=<COMFYUI_ROOT>/VoiceDesign-QwenTTS.json \
  uv run uvicorn main:app --host 127.0.0.1 --port 8090     # 在 ~/qwentts-shim 下
curl -s http://127.0.0.1:8090/health          # {"status": "ok"}
```

### 4.3 music-shim

```bash
cp -r scripts/dgx-shims/music-shim ~/music-shim
cd ~/music-shim && uv sync
```

同样认环境变量:

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `COMFYUI_HTTP` / `COMFYUI_WS` | `127.0.0.1:8188` | 同上 |
| `WORKFLOW_JSON_PATH` | 参考机专属路径 | **必设**:指到你的 `MusicCreation-ACESTEP1.5XL_api.json` |
| `FFMPEG_BIN` | 参考机专属路径 | **建议设**:指到带 libmp3lame 的 ffmpeg 绝对路径 |
| `MUSIC_SHIM_POLL_TIMEOUT_S` | `300` | 生成超时 |

```bash
WORKFLOW_JSON_PATH=<COMFYUI_ROOT>/MusicCreation-ACESTEP1.5XL_api.json \
FFMPEG_BIN=$(command -v ffmpeg) \
  uv run uvicorn main:app --host 127.0.0.1 --port 8092     # 在 ~/music-shim 下
curl -s http://127.0.0.1:8092/health          # {"status": "ok"}
```

---

## 5. systemd 托管(user 服务)

三个 unit 同一个模式,放 `~/.config/systemd/user/`。下面是 image 的模板,另两个把**目录、端口、环境变量**换成 §4.2 / §4.3 的即可。

`~/.config/systemd/user/shanhai-image.service`:

```ini
[Unit]
Description=shanhai image shim (ComfyUI bridge)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=%h/image-shim
ExecStart=%h/image-shim/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8091
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
```

qwentts / music 的 unit 额外用 `Environment=` 注入 §4 表里的变量,例如:

```ini
# shanhai-music.service 的 [Service] 段追加:
WorkingDirectory=%h/music-shim
Environment="WORKFLOW_JSON_PATH=<COMFYUI_ROOT>/MusicCreation-ACESTEP1.5XL_api.json"
Environment="FFMPEG_BIN=%h/…/ffmpeg"
ExecStart=%h/music-shim/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8092
```

启用:

```bash
systemctl --user daemon-reload
systemctl --user enable --now shanhai-image shanhai-tts shanhai-music
loginctl enable-linger "$USER"     # 没这条,注销后服务被杀
```

> image-shim 因为地址/目录是**改在源码里**的,unit 不需要为它注入 ComfyUI 相关环境变量。

---

## 6. 接回 shanhai

在 shanhai 应用的 `.env` 里把三类端点指到本机 shim。**base_url 要带 `/v1` 后缀**(路由是 `/v1/...`):

```bash
# 图像 —— 注意 API_MODE 必须是 images_api(shim 走 /images/generations|edits 契约,
#         不是默认的 chat_api),否则 shanhai 会用错调用形态。
SHANHAI_IMAGE_BASE_URL=http://127.0.0.1:8091/v1
SHANHAI_IMAGE_API_MODE=images_api
SHANHAI_IMAGE_MODEL=comfyui-local          # shim 忽略此值,填个占位即可

# 配音 —— voice 值必须是 shim 认识的 key(见 qwentts VOICE_DESCRIPTIONS)
SHANHAI_TTS_BASE_URL=http://127.0.0.1:8090/v1
SHANHAI_TTS_MODEL=qwen3-tts-voicedesign    # shim 忽略,占位
SHANHAI_TTS_VOICES=女声,男声,EN-Female,EN-Male   # 登记到新建表单下拉
SHANHAI_TTS_VOICE=女声                     # 中文轨默认
SHANHAI_TTS_VOICE_EN=EN-Female             # 英文轨默认

# 背景音乐
SHANHAI_MUSIC_BASE_URL=http://127.0.0.1:8092/v1
SHANHAI_MUSIC_MODEL=ace-step-v1.5xl
```

改完重启 shanhai-web(`EnvironmentFile` 只在启动时读一次)。四类端点各自独立、可混搭:LLM 仍走云端、只有图像/语音/音乐走本地 shim 完全可以。

---

## 7. 冒烟验证

先各 `curl /health` 拿到 ok(证明 shim↔ComfyUI 通),再逐个真跑一次:

```bash
# 图像:生成一张,期望 data[0].b64_json
curl -s -X POST http://127.0.0.1:8091/v1/images/generations \
  -H 'content-type: application/json' \
  -d '{"prompt":"a red apple on a table","size":"1024x1024"}' | head -c 120

# 配音:内置女声,期望返回 mp3 字节
curl -s -X POST http://127.0.0.1:8090/v1/audio/speech \
  -H 'content-type: application/json' \
  -d '{"voice":"女声","input":"测试一下配音"}' --output /tmp/tts.mp3 && file /tmp/tts.mp3

# 音乐:10 秒纯器乐,期望 mp3
curl -s -X POST http://127.0.0.1:8092/v1/audio/music \
  -H 'content-type: application/json' \
  -d '{"prompt":"calm piano","duration_s":10}' --output /tmp/bgm.mp3 && file /tmp/bgm.mp3
```

三个都出正常产物后,再从 shanhai 建一个作品跑 S3/S4/S5 端到端。

---

## 8. 排错

| 症状 | 原因 | 处理 |
|---|---|---|
| `/health` 返回 `ok:false` / 503 / 502 | ComfyUI 没起或地址错 | 先 `curl 127.0.0.1:8188/system_stats`;确认 `COMFYUI_HTTP`/image 源码里的地址 |
| 请求 500,信息含「工作流模板缺失」 | §2.2 的 JSON 没放对位置 | 核对 `WORKFLOW_JSON_PATH` / image 源码 `COMFYUI_ROOT` 指向的目录里有没有那个文件 |
| 500,克隆音色相关 | 缺 `VoiceClone-QwenTTS.json` | 放到 qwentts-shim 目录,或先别用 `clone:` 音色 |
| 502「ComfyUI 拒绝提交任务」 | 工作流引用了 ComfyUI 里不存在的节点/模型 | 缺自定义节点或权重,回到 §2.3 补 |
| music/克隆音频 500,ffmpeg 报错 | ffmpeg 无 libmp3lame | 换完整版并设 `FFMPEG_BIN` |
| 504 超时 | 共享 GPU 排队严重 | 调大 `*_POLL_TIMEOUT_S`;或错峰 |
| 配音/音乐永久挂起到超时 | (仅自建变体)先提交后连 WebSocket,漏掉完成事件 | 现版本已是「先连后提交」,别改动这个顺序 |
| 「重绘/重配音」永远拿回同一份 | ComfyUI 节点缓存 + 模板 seed 写死 | 现版本三个 shim 都有 `_randomize_seeds`,别删;副作用是同提示词也真跑(不再秒回) |

日常起停、看日志见 [`ops-dgx.md`](ops-dgx.md)(把服务名换成你自己的)。
</content>
