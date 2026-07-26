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
import random
import uuid
from pathlib import Path

import httpx
import websockets
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

COMFYUI_HTTP = os.getenv("COMFYUI_HTTP", "http://127.0.0.1:8188")
COMFYUI_WS = os.getenv("COMFYUI_WS", "ws://127.0.0.1:8188/ws")
# 只读:直接引用队友(wuzi)维护的工作流模板,不拷贝副本(同 image-shim/music-shim 做法),
# 避免模板更新后两处失配。
WORKFLOW_PATH = Path(os.getenv(
    "WORKFLOW_JSON_PATH",
    "/home1/wuzi/WanderInk/comfyui-bridge/VoiceDesign-QwenTTS.json"))
POLL_TIMEOUT_S = float(os.getenv("QWENTTS_SHIM_POLL_TIMEOUT_S", "180"))

# 节点 ID 常量:来自实测确认的工作流结构(curl /object_info 逐一核实)。
NODE_TEXT = "75"       # Text Multiline —— 要合成的文本
NODE_VOICE_DESC = "76"  # Text Multiline —— 声音设计提示词(英文)
NODE_TTS = "73"        # Qwen3TTSVoiceDesign —— 承载"语速"字段

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


def _load_workflow() -> dict:
    if not WORKFLOW_PATH.exists():
        raise HTTPException(500, f"工作流模板缺失: {WORKFLOW_PATH}(队友 comfyui-bridge 目录结构是否变动?)")
    return _randomize_seeds(json.loads(WORKFLOW_PATH.read_text(encoding="utf-8")))


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


@app.post("/v1/audio/speech")
async def synthesize(req: SpeechRequest) -> Response:
    if not req.input.strip():
        raise HTTPException(400, "input 不能为空")
    client_id = str(uuid.uuid4())
    wf = _build_workflow(req)
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
    # SaveAudioAdvanced 工作流节点(77)配置的 format 就是 "mp3",无需再转码。
    return Response(content=raw, media_type="audio/mpeg")


@app.get("/v1/models")
def list_models() -> dict:
    return {"object": "list", "data": [{"id": "qwen3-tts-voicedesign", "object": "model"}]}
