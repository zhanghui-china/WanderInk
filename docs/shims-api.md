# 三个 ComfyUI shim · 接口文档

`image` / `qwentts` / `music` 三个 shim 对外暴露的 HTTP 接口。本文档以源码为准
(`scripts/dgx-shims/<shim>/main.py`),字段、默认值、状态码均逐条核对过。

- 部署方式见 [deploy-shims.md](deploy-shims.md);对局域网开放这三个接口见 [deploy-gateway.md](deploy-gateway.md);存档与设计说明见 [../scripts/dgx-shims/README.md](../scripts/dgx-shims/README.md)。
- 调用方是 shanhai 的 `src/shanhai/providers/{image,tts,music}.py`,契约见 §5。

---

## 1. 通用约定

| 项 | 说明 |
|---|---|
| 服务 | image `:8091` · qwentts `:8090` · music `:8092`(均 FastAPI + uvicorn) |
| 路由前缀 | 业务接口一律 `/v1/...`(OpenAI 兼容);**`/health` 在根路径,不带 `/v1`** |
| 鉴权 | **无。** 三个 shim 不校验任何 token。三者本身仍只监听 `127.0.0.1`,⚠️ 别改绑 `0.0.0.0`;但**若部署了网关**(`:8099`,见 [deploy-gateway.md](deploy-gateway.md)),它们会经网关间接暴露到局域网,此时安全边界完全落在网络层 |
| 调用语义 | **同步阻塞**:一个请求 = 一次完整生成,响应返回时产物已就绪。内部是 ComfyUI 排队协议,但对调用方不暴露任务 id、无需轮询 |
| 并发 | 单进程 async;真正的串行点在 ComfyUI(共享 GPU 排队),shim 本身不限流 |
| 错误体 | FastAPI 默认形状:`{"detail": "<中文说明>"}`(`/health` 的 503 除外,见 §2.1) |
| 幂等/缓存 | 每次请求都会把工作流里写死的 `seed` 换成随机值(`_randomize_seeds`),因此**同样的输入也会产出不同结果**。这是刻意的:否则 ComfyUI 的节点缓存会让"重绘/重配音"永远拿回上一次那份产物 |

**ComfyUI 依赖**:三者都要求 ComfyUI 可达(默认 `127.0.0.1:8188`)且工作流模板存在。缺任一即报错,见 §6。

---

## 2. image-shim(`:8091`)

图像生成与编辑,OpenAI `images_api` 契约。**与另两个不同,它走纯 HTTP 轮询**(不用 WebSocket),轮询上限 240s、间隔 2s。

### 2.1 `GET /health`

探测 ComfyUI 是否可达(`GET /system_stats`)。

| 情况 | 状态码 | 响应体 |
|---|---|---|
| 正常 | 200 | `{"ok": true}` |
| ComfyUI 返回非 200 | 200 | `{"ok": false}` ⚠️ 注意仍是 200 |
| 连不上 ComfyUI | 503 | `{"ok": false, "error": "<异常>"}` |

> ⚠️ 只判 `ok` 字段,别只判 HTTP 状态码——ComfyUI 活着但异常时这里是 `200 + ok:false`。

### 2.2 `POST /v1/images/generations` — 文生图

`Content-Type: application/json`

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `prompt` | string | ✅ | — | 空或纯空白 → 400 |
| `size` | string | | `"1536x1024"` | `"宽x高"`,映射到画幅档位见 §2.4 |
| `lora` | string | | — | **这条路径上被静默忽略**:文生图模板没有 LoRA 节点 |

其它字段(如 `model`、`n`)会被忽略,不报错。

**响应** `200`:
```json
{ "data": [{ "b64_json": "<base64 PNG>" }] }
```

```bash
curl -s -X POST http://127.0.0.1:8091/v1/images/generations \
  -H 'content-type: application/json' \
  -d '{"prompt":"水墨风格,雷峰塔前的白蛇","size":"1536x1024"}'
```

### 2.3 `POST /v1/images/edits` — 带参考图生成

`Content-Type: multipart/form-data`

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `prompt` | 表单字段 | ✅ | — | 空 → 400 |
| `size` | 表单字段 | | `"1536x1024"` | 同上 |
| `image[]` | 文件(可重复) | ✅ | — | 至少一个,否则 400。**只取前 3 张**,第 4 张起静默丢弃 |
| `lora` | 表单字段 | | 默认 LoRA | 见 §2.5 |

参考图张数决定使用哪套工作流(1/2/3 张各一套模板,节点号不同)。

**响应**:同 §2.2。

```bash
curl -s -X POST http://127.0.0.1:8091/v1/images/edits \
  -F 'prompt=白蛇立于塔前' -F 'size=1536x1024' \
  -F 'image[]=@char_a.png' -F 'image[]=@char_b.png'
```

### 2.4 size → 画幅档位

底层 ComfyUI 的 ResolutionSelector 只接受 **8 个固定宽高比**,shim 按比值取最接近的一档:

`1:1` · `4:3` · `3:4` · `3:2` · `2:3` · `16:9` · `9:16` · `21:9`

- `size` 解析失败(非 `WxH`、除零)→ 回落 `16:9`,不报错。
- ⚠️ **最宽只到 21:9 ≈ 2.33**。请求更宽的比例会静默返回 2.33,由调用方自行裁切。

### 2.5 lora 取值

短名(大小写不敏感)映射到 safetensors 文件名:

| 短名 | 文件 |
|---|---|
| `real_ani_qwen` | `Real_Ani-Qwen_000001250.safetensors`(**默认**) |
| `figurine_qwen` | `figurine_qwen.safetensors` |
| `bjd.7arl` | `bjdE5A883E5A883V2004.7ARL.safetensors` |

- 直接传 `.safetensors` 结尾的文件名 → 原样透传。
- 不传、或传了不认识的值 → **回落默认**,不报错(工作流里 LoRA 节点是焊死的,不存在"不用 LoRA")。

---

## 3. qwentts-shim(`:8090`)

语音合成(Qwen3-TTS)。走 ComfyUI **WebSocket** 协议,超时默认 180s(`QWENTTS_SHIM_POLL_TIMEOUT_S`)。

### 3.1 `GET /health`

| 情况 | 状态码 | 响应体 |
|---|---|---|
| 正常 | 200 | `{"status": "ok"}` |
| ComfyUI 不可达 | 502 | `{"detail": "ComfyUI 不可达: ..."}` |

### 3.2 `POST /v1/audio/speech` — 合成语音

`Content-Type: application/json`

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `input` | string | ✅ | — | 要合成的文本;空或纯空白 → 400 |
| `voice` | string | | `"女声"` | 见 §3.3 |
| `speed` | float | | `1.0` | 语速,**钳制到 [0.5, 2.0]**(超出不报错,直接夹住) |
| `model` | string | | `""` | **忽略**(工作流固定),保留仅为兼容 OpenAI 客户端 |
| `response_format` | string | | `"mp3"` | **忽略**,输出恒为 mp3 |

**响应** `200`:**原始 mp3 字节流**,`Content-Type: audio/mpeg`(不是 JSON)。

```bash
curl -s -X POST http://127.0.0.1:8090/v1/audio/speech \
  -H 'content-type: application/json' \
  -d '{"voice":"女声","input":"白娘子端坐塔前","speed":1.0}' --output out.mp3
```

### 3.3 voice 取值

**内置音色**(语种由音色隐含,无需额外 language 参数):

| voice | 说明 |
|---|---|
| `女声` | 年轻女声,普通话(默认) |
| `男声` | 中年男声,普通话 |
| `EN-Female` | 年轻女声,美式英语 |
| `EN-Male` | 中年男声,美式英语 |

> 未知的内置音色**静默回落到 `女声`**,不报错。

**克隆音色**:`clone:<文件名>`,文件名由 §3.4 注册时返回。

- 句柄含 `/`、`\`、`..` 或为空 → **400**(不做静默降级:克隆音色一旦回落,用户会拿到完全不认识的嗓子却毫无提示)。
- ⚠️ 克隆链路**没有语速节点**,`speed` 改由 ffmpeg `atempo` 后处理实现;若 ffmpeg 调速失败,**返回原速音频并在服务端打 warn**,不报错。

### 3.4 `POST /v1/voices/clone` — 注册克隆音色

`Content-Type: multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `file` | 文件 | ✅ | 参考录音(wav 最稳);空文件 → 400 |

**响应** `200`:
```json
{ "voice": "clone:shanhai_voice_a1b2c3d4e5f6.wav" }
```

把这个 `voice` 原样传给 §3.2 即可。**shim 自身不存任何状态**——这个字符串本身就是句柄。

> 已知欠账:ComfyUI 没有删除接口,注册过的参考音频会持续堆积在它的 `input/`,需定期人工清理。

### 3.5 `GET /v1/models`

```json
{ "object": "list", "data": [
  { "id": "qwen3-tts-voicedesign", "object": "model" },
  { "id": "qwen3-tts-voiceclone",  "object": "model" } ] }
```
静态列表,仅供 OpenAI 客户端探活/枚举,`model` 字段实际不被使用。

---

## 4. music-shim(`:8092`)

背景音乐生成(ACE-Step)。走 ComfyUI **WebSocket** 协议,超时默认 300s(`MUSIC_SHIM_POLL_TIMEOUT_S`)。

### 4.1 `GET /health`

同 §3.1:正常 `200 {"status":"ok"}`,ComfyUI 不可达 `502`。

### 4.2 `POST /v1/audio/music` — 生成音乐

`Content-Type: application/json`

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `prompt` | string | ✅ | — | **风格标签文本**(不是歌词),如 `"calm guqin, misty"` |
| `duration_s` | float | ✅ | — | 目标时长(秒) |
| `lyrics` | string | | `"[instrumental]"` | 歌词;默认纯器乐 |
| `bpm` | int \| null | | `80` | 传 `null` 或不传即用默认 |
| `model` | string | | `"ace-step-v1.5xl"` | **忽略**(工作流固定) |

另有三个**写死不可调**的参数:拍号 `4`、语言 `zh`、调式 `C major`。

**响应** `200`:**原始 mp3 字节流**,`Content-Type: audio/mpeg`。

> ComfyUI 侧输出未必是 mp3(可能 wav/flac),shim **每次都用 ffmpeg 统一转码 mp3**,所以 ffmpeg 必须带 libmp3lame(见 deploy-shims.md §3.2)。

```bash
curl -s -X POST http://127.0.0.1:8092/v1/audio/music \
  -H 'content-type: application/json' \
  -d '{"prompt":"calm guqin, misty mountains","duration_s":30}' --output bgm.mp3
```

---

## 5. 调用方契约(shanhai 侧发什么)

| shim | 调用方 | 实际发送 | shim 忽略的字段 |
|---|---|---|---|
| image(文生图) | `providers/image.py::_via_generations` | `model` `prompt` `size` `n` `lora?` | `model` `n` |
| image(带参考图) | `providers/image.py::_via_edits` | multipart:`model` `prompt` `size` `lora?` + N 个 `image[]` | `model` |
| qwentts | `providers/tts.py::synthesize` | `model` `voice` `input` `response_format` `speed` | `model` `response_format` |
| qwentts(注册) | `providers/tts.py::register_clone_voice` | multipart `file` | — |
| music | `providers/music.py::generate` | `model` `prompt` `lyrics` `duration_s` `bpm?` | `model` |

`base_url` 一律配到 **带 `/v1` 后缀**(如 `http://127.0.0.1:8091/v1`),因为路由本身是 `/v1/...`。图像还须设 `SHANHAI_IMAGE_API_MODE=images_api`(默认的 `chat_api` 形态与本 shim 不兼容)。完整环境变量见 [deploy-shims.md](deploy-shims.md) §6。

---

## 6. 错误码总表

| 码 | 触发条件 | 常见原因 |
|---|---|---|
| **400** | 参数不合法 | `prompt`/`input` 为空;`images/edits` 没带 `image[]`;克隆音色句柄非法;上传空文件 |
| **500** | `工作流模板缺失: <path>` | 模板 JSON 不在配置的目录(队友改版/搬家最常见) |
| **500** | `ComfyUI 输出中未找到音频节点结果` | 工作流跑完但没有音频输出节点 |
| **502** | `ComfyUI 拒绝提交任务` | 工作流引用了 ComfyUI 里不存在的节点/模型 |
| **502** | `ComfyUI WebSocket 连接失败` / `提交 ComfyUI 工作流失败` | ComfyUI 挂了、地址错、`/ws` 不通 |
| **502** | `ComfyUI 执行完成但无图像输出` | 图像工作流没有输出节点 |
| **502** | `上传参考音频到 ComfyUI 失败` | ComfyUI `/upload/image` 不可用 |
| **502** | `ComfyUI 不可达`(仅 `/health`) | qwentts / music 的健康检查失败 |
| **503** | `/health` 返回 `ok:false` | image 的健康检查连不上 ComfyUI |
| **504** | `等待 ComfyUI 渲染超时` / `语音合成超时` / `音乐生成超时` | **共享 GPU 排队**最常见;调大对应超时环境变量或错峰 |

排错步骤见 [deploy-shims.md](deploy-shims.md) §8。

---

## 7. 已知限制

- **无鉴权、无限流**:安全完全依赖只监听 `127.0.0.1`。
- **同步长请求**:单请求可达数分钟(排队+生成),调用方必须把客户端超时设得比 shim 的轮询上限更大。shanhai 侧 `SHANHAI_IMAGE_TIMEOUT` 默认 300s > shim 的 240s。
- **无任务查询/取消接口**:请求一旦发出,只能等或断开;断开不会取消 ComfyUI 里已排队的任务。
- **节点号写死**:工作流模板改版后,shim 里的节点常量需同步修改(见各 `main.py` 顶部)。
- **临时文件堆积**:参考图与克隆音频会持续累积在 ComfyUI 的 `input/`,ComfyUI 无删除接口,需人工清理。
- **image-shim 的 ComfyUI 地址与模板目录是硬编码常量**(另两个走环境变量),换机器要改源码。
