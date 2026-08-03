"""qwentts-shim: FastAPI 转发到本机 ComfyUI(Qwen3-TTS VoiceDesign 工作流),替换原 CosyVoice2。
同 image-shim/music-shim 的部署哲学:独立 systemd 服务、/health 探活、错误尽量转成明确的
HTTP 状态码而不是裸异常。语音合成走 ComfyUI 的 WebSocket 排队协议(非 HTTP 轮询),
websocket 连接顺序与 music-shim 同款(先连后提交,避免命中缓存瞬间完成导致错过事件)。
DGX 专属运维脚本,不纳入 shanhai git 仓库版本控制(与 image-shim/music-shim/tts_shim.py 现状一致)。

对外契约与原 tts_shim.py(CosyVoice2)完全一致:POST /v1/audio/speech,
{model, voice, input, response_format, speed} → 原始 mp3 字节,shanhai 侧 TTSClient 不用改代码。
"""
import copy
import json
import os
import subprocess
import tempfile
import random
import uuid
from pathlib import Path

import httpx
import websockets
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

COMFYUI_HTTP = os.getenv("COMFYUI_HTTP", "http://127.0.0.1:8188")
COMFYUI_WS = os.getenv("COMFYUI_WS", "ws://127.0.0.1:8188/ws")
# 只读:直接引用队友(wuzi)维护的工作流模板,不拷贝副本(同 image-shim/music-shim 做法),
# 避免模板更新后两处失配。
WORKFLOW_PATH = Path(os.getenv(
    "WORKFLOW_JSON_PATH",
    "/home1/wuzi/WanderInk/comfyui-bridge/VoiceDesign-QwenTTS.json"))
# 音色克隆模板。**这个拷了副本**,与上面"不拷副本"的选择相反,理由:VoiceClone 模板是队友
# 2026-07-27 才新加的、还在动(同目录其它模板都停在 07-14),线上直接引用等于把生产挂在
# 别人的编辑器上。VoiceDesign 已经稳定三周,继续直引。
CLONE_WORKFLOW_PATH = Path(os.getenv(
    "CLONE_WORKFLOW_JSON_PATH", str(Path(__file__).parent / "VoiceClone-QwenTTS.json")))
POLL_TIMEOUT_S = float(os.getenv("QWENTTS_SHIM_POLL_TIMEOUT_S", "180"))

# 节点 ID 常量:来自实测确认的工作流结构(curl /object_info 逐一核实)。
NODE_TEXT = "75"       # Text Multiline —— 要合成的文本
NODE_VOICE_DESC = "76"  # Text Multiline —— 声音设计提示词(英文)
NODE_TTS = "73"        # Qwen3TTSVoiceDesign —— 承载"语速"字段

# VoiceClone 模板的节点(结构与 VoiceDesign 完全不同,字段名是英文的)
CLONE_NODE_AUDIO = "151"   # LoadAudio —— 参考音频,inputs.audio 是 ComfyUI input/ 下的纯文件名
CLONE_NODE_TEXT = "153"    # Text Multiline —— 要合成的文本
# 注意:VoiceClone 链路**没有语速节点**(参考音频决定语速),speed 只能在拿到音频后用
# ffmpeg atempo 后处理,见 _apply_speed。
CLONE_PREFIX = "clone:"    # voice 值以此开头即走克隆;后面跟 ComfyUI input/ 里的文件名
FFMPEG = os.getenv("FFMPEG_BIN", "ffmpeg")

# 音色 -> 声音设计提示词(必须是英文,模型只认这个语言的描述效果最好)。
# 键名直接用中文,前端下拉框(meta.voices)据此渲染,无需额外的 label 映射代码。
# 语种由 voice key 隐含(EN- 前缀那两个是英文轨用的),shanhai 侧 TTSClient 不需要
# 额外的 language 参数——与 CosyVoice2→Qwen3-TTS 那次切换同样是"客户端零代码改动"。
VOICE_DESCRIPTIONS: dict[str, str] = {
    "女声": "A young female voice, clear and gentle, standard Mandarin accent.",
    "男声": "A middle-aged male voice, deep and calm, standard Mandarin accent.",
    "EN-Female": "A young female voice, warm and clear, native American English accent, "
                 "narrating a story for visitors.",
    "EN-Male": "A middle-aged male voice, deep and calm, native American English accent, "
               "narrating a story for visitors.",
}
DEFAULT_VOICE = "女声"

app = FastAPI(title="shanhai-qwentts-shim")


class SpeechRequest(BaseModel):
    model: str = ""              # 忽略:工作流固定加载 VoiceDesign 模型,字段保留兼容 TTSClient
    voice: str = DEFAULT_VOICE
    input: str
    response_format: str = "mp3"
    speed: float = 1.0


def _randomize_seeds(workflow: dict) -> dict:
    """把工作流里所有写死的 seed 换成随机值。

    ComfyUI 对"输入完全相同"的节点有执行缓存,而模板里的 seed 是常量——于是同一段
    文案永远拿回上一次那份音频,"重配音"从机制上就不可能出新结果(2026-07-26 实测:
    同一段文案连发两次返回字节完全相同、第二次 0 秒返回;换文案则要 11 秒真跑)。
    这也让 shanhai 侧 s5_audio 的"三试取最长"截断重试彻底空转。

    遍历全部节点而不是写死节点号:模板改版或新增采样器都不会漏。值是 link 的跳过。"""
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if isinstance(inputs, dict) and isinstance(inputs.get("seed"), int):
            inputs["seed"] = random.randrange(2**53)
    return workflow


def _load_workflow(path: Path = WORKFLOW_PATH) -> dict:
    if not path.exists():
        raise HTTPException(500, f"工作流模板缺失: {path}(队友 comfyui-bridge 目录结构是否变动?)")
    return _randomize_seeds(json.loads(path.read_text(encoding="utf-8")))


def _build_clone_workflow(req: SpeechRequest) -> dict:
    """克隆音色:voice 形如 "clone:<ComfyUI input 里的文件名>",该文件名由 /v1/voices/clone
    注册时返回。shim 本身不存任何状态——voice 字符串自己就是句柄。"""
    ref = req.voice[len(CLONE_PREFIX):].strip()
    # 不做静默降级:未知的内置音色回落到女声是无害的,但克隆音色一旦回落,用户会拿到一个
    # 完全不认识的嗓子却毫无提示,只会以为"克隆没生效"。宁可明确报错。
    if not ref or "/" in ref or "\\" in ref or ".." in ref:
        raise HTTPException(400, f"非法的克隆音色句柄: {req.voice!r}")
    wf = copy.deepcopy(_load_workflow(CLONE_WORKFLOW_PATH))
    wf[CLONE_NODE_AUDIO]["inputs"]["audio"] = ref
    wf[CLONE_NODE_TEXT]["inputs"]["text"] = req.input
    return wf


def _build_workflow(req: SpeechRequest) -> dict:
    # 每请求现读+深拷贝模板,避免并发请求互相污染共享字典(FastAPI async 单进程内交错执行)。
    wf = copy.deepcopy(_load_workflow())
    wf[NODE_TEXT]["inputs"]["text"] = req.input
    wf[NODE_VOICE_DESC]["inputs"]["text"] = VOICE_DESCRIPTIONS.get(req.voice, VOICE_DESCRIPTIONS[DEFAULT_VOICE])
    # 语速节点范围 [0.5, 2.0](见 object_info),shanhai 侧 speed 常用范围 0.8~1.2,天然落在区间内。
    wf[NODE_TTS]["inputs"]["语速"] = max(0.5, min(2.0, req.speed))
    return wf


async def _queue_prompt(client: httpx.AsyncClient, wf: dict, client_id: str) -> str:
    r = await client.post(f"{COMFYUI_HTTP}/prompt", json={"prompt": wf, "client_id": client_id})
    r.raise_for_status()
    return r.json()["prompt_id"]


async def _wait_for_completion(ws, prompt_id: str) -> None:
    """在已连接的 WebSocket 上监听本次 prompt_id 的 executing 事件(node=None 即完成)。
    调用方必须在提交 /prompt **之前**就已建立并传入这条连接(与 music-shim 同款教训:
    若先提交再连接,ComfyUI 命中节点缓存时近乎瞬间完成,会错过完成事件永久挂起直至超时)。"""
    import asyncio

    async def _listen() -> None:
        async for raw in ws:
            if isinstance(raw, bytes):
                continue  # 二进制预览帧,非本次关心的 executing 事件
            msg = json.loads(raw)
            if msg.get("type") == "executing":
                data = msg.get("data", {})
                if data.get("prompt_id") == prompt_id and data.get("node") is None:
                    return

    try:
        await asyncio.wait_for(_listen(), timeout=POLL_TIMEOUT_S)
    except TimeoutError as e:
        raise HTTPException(504, f"语音合成超时(>{POLL_TIMEOUT_S}s)") from e
    except websockets.exceptions.WebSocketException as e:
        raise HTTPException(502, f"ComfyUI WebSocket 连接失败: {e}") from e


async def _fetch_audio(client: httpx.AsyncClient, prompt_id: str) -> bytes:
    r = await client.get(f"{COMFYUI_HTTP}/history/{prompt_id}")
    r.raise_for_status()
    outputs = r.json().get(prompt_id, {}).get("outputs", {})
    for node_out in outputs.values():
        for audio in node_out.get("audio", []):
            params = {"filename": audio["filename"], "subfolder": audio.get("subfolder", ""),
                      "type": audio.get("type", "output")}
            resp = await client.get(f"{COMFYUI_HTTP}/view", params=params)
            resp.raise_for_status()
            return resp.content
    raise HTTPException(500, "ComfyUI 输出中未找到音频节点结果")


@app.get("/health")
async def health() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{COMFYUI_HTTP}/system_stats")
            r.raise_for_status()
    except Exception as e:
        raise HTTPException(502, f"ComfyUI 不可达: {e}") from e
    return {"status": "ok"}


def _apply_speed(mp3: bytes, speed: float) -> bytes:
    """VoiceClone 链路没有语速节点(语速由参考音频决定),只能事后用 atempo 调。
    VoiceDesign 那条路不走这里——它的语速在工作流里就调好了,再过一道只会白白掉音质。
    atempo 单次有效范围是 [0.5, 2.0],与 VoiceDesign 语速节点的范围一致,故不需要串联多级。"""
    if abs(speed - 1.0) < 0.01:
        return mp3
    tempo = max(0.5, min(2.0, speed))
    with tempfile.TemporaryDirectory() as td:
        src, out = Path(td) / "in.mp3", Path(td) / "out.mp3"
        src.write_bytes(mp3)
        r = subprocess.run(
            [FFMPEG, "-y", "-i", str(src), "-filter:a", f"atempo={tempo:g}",
             "-c:a", "libmp3lame", "-q:a", "2", str(out)],
            capture_output=True)
        if r.returncode != 0 or not out.exists():
            # 调速失败不该让整段配音失败——原速音频仍然可用,只是语速没生效。
            print(f"[warn] atempo 失败,返回原速音频: {r.stderr[-200:]!r}", flush=True)
            return mp3
        return out.read_bytes()


@app.post("/v1/voices/clone")
async def register_clone_voice(file: UploadFile = File(...)) -> dict:
    """把一段参考音频注册成音色句柄。做法与 image-shim 的 _upload_reference 同款:
    纯 HTTP 传到 ComfyUI 的 input/(不需要 wuzi 目录的写权限,ComfyUI 进程自己写)。

    ComfyUI 没有 /upload/audio,但 /upload/image 不校验类型、写的是裸字节,队友那边
    已经用它传音频跑通过;而 LoadAudio 走 PyAV 解码,wav 自然能读。
    已知欠账:ComfyUI 无删除接口,这些参考音频会持续累积在 input/,需要定期清理。"""
    data = await file.read()
    if not data:
        raise HTTPException(400, "空文件")
    name = f"shanhai_voice_{uuid.uuid4().hex[:12]}.wav"
    async with httpx.AsyncClient(timeout=60) as c:
        try:
            r = await c.post(f"{COMFYUI_HTTP}/upload/image",
                             files={"image": (name, data, "audio/wav")},
                             data={"overwrite": "true"})
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise HTTPException(502, f"上传参考音频到 ComfyUI 失败: {e}") from e
    return {"voice": CLONE_PREFIX + r.json()["name"]}


@app.post("/v1/audio/speech")
async def synthesize(req: SpeechRequest) -> Response:
    if not req.input.strip():
        raise HTTPException(400, "input 不能为空")
    client_id = str(uuid.uuid4())
    is_clone = req.voice.startswith(CLONE_PREFIX)
    wf = _build_clone_workflow(req) if is_clone else _build_workflow(req)
    uri = f"{COMFYUI_WS}?clientId={client_id}"
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            # 顺序至关重要:先连 WebSocket、连上之后才提交任务(同 music-shim 教训)。
            async with websockets.connect(uri, open_timeout=10) as ws:
                try:
                    prompt_id = await _queue_prompt(client, wf, client_id)
                except httpx.HTTPError as e:
                    raise HTTPException(502, f"提交 ComfyUI 工作流失败: {e}") from e
                await _wait_for_completion(ws, prompt_id)
        except OSError as e:
            raise HTTPException(502, f"ComfyUI WebSocket 连接失败: {e}") from e
        raw = await _fetch_audio(client, prompt_id)
    # SaveAudioAdvanced 两个模板配的 format 都是 "mp3",无需转码;克隆链路没有语速节点,
    # 语速只能在这里补(见 _apply_speed)。
    if is_clone:
        raw = _apply_speed(raw, req.speed)
    return Response(content=raw, media_type="audio/mpeg")


@app.get("/v1/models")
def list_models() -> dict:
    return {"object": "list", "data": [{"id": "qwen3-tts-voicedesign", "object": "model"},
                                   {"id": "qwen3-tts-voiceclone", "object": "model"}]}
