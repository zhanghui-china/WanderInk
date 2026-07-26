"""music-shim: FastAPI 转发到本机 ComfyUI(ACE-Step 音乐生成工作流)。
同 image-shim/tts_shim 的部署哲学:独立 systemd 服务、/health 探活、错误尽量转成明确的
HTTP 状态码而不是裸异常。音乐生成走 ComfyUI 的 WebSocket 排队协议(非 HTTP 轮询),
参考 wuzi 的 ~/ComfyUI/generate_music_api.py(只读不可改,本文件是异步 FastAPI 重写)。
DGX 专属运维脚本,不纳入 shanhai git 仓库版本控制(与 image-shim/tts_shim.py 现状一致)。
"""
import asyncio
import copy
import json
import os
import random
import subprocess
import tempfile
import uuid
from pathlib import Path

import httpx
import websockets
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

COMFYUI_HTTP = os.getenv("COMFYUI_HTTP", "http://127.0.0.1:8188")
COMFYUI_WS = os.getenv("COMFYUI_WS", "ws://127.0.0.1:8188/ws")
# 只读:直接引用队友(wuzi)维护的工作流模板,不拷贝副本(同 image-shim 的
# COMFYUI_ROOT 做法),避免模板更新后两处失配。
WORKFLOW_PATH = Path(os.getenv(
    "WORKFLOW_JSON_PATH", "/home1/wuzi/ComfyUI/MusicCreation-ACESTEP1.5XL_api.json"))
POLL_TIMEOUT_S = float(os.getenv("MUSIC_SHIM_POLL_TIMEOUT_S", "300"))
# 共享机的 /usr/local/bin/ffmpeg 是残缺构建(无 libmp3lame,详见 docs/deploy-dgx.md);
# 必须走独立的完整版 conda 环境,同 tts_shim.py 已踩过的坑。
FFMPEG_BIN = os.getenv("FFMPEG_BIN", str(Path.home() / "anaconda3/envs/shanhai-ffmpeg/bin/ffmpeg"))
DEFAULT_BPM = 80
DEFAULT_TIMESIG = "4"
DEFAULT_LANGUAGE = "zh"
DEFAULT_KEYSCALE = "C major"

# 节点 ID 常量:来自实测确认的工作流结构(python3 -c 直接读 workflow json 逐一核实)。
NODE_LYRICS = "40"        # Text Multiline
NODE_STYLE = "41"         # Text Multiline
NODE_DURATION = "43"      # PrimitiveFloat
NODE_BPM = "45"           # PrimitiveInt
NODE_AUDIO_ENCODE = "36"  # TextEncodeAceStepAudio1.5(timesignature/language/keyscale)

app = FastAPI(title="shanhai-music-shim")


def _randomize_seeds(workflow: dict) -> dict:
    """把工作流里所有写死的 seed 换成随机值。

    ComfyUI 对"输入完全相同"的节点有执行缓存,而模板里的 seed 是常量——同一批风格标签
    永远拿回上一次那段 BGM(image/tts 两个 shim 已实测坐实这个行为)。对配乐而言还多一层
    影响:同情绪的作品会共用一模一样的曲子,毫无变化。

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
        raise HTTPException(500, f"工作流模板缺失: {WORKFLOW_PATH}(队友 ComfyUI 目录结构是否变动?)")
    return _randomize_seeds(json.loads(WORKFLOW_PATH.read_text(encoding="utf-8")))


class MusicRequest(BaseModel):
    model: str = "ace-step-v1.5xl"   # 目前忽略,工作流固定;字段保留为未来多工作流选型占位
    prompt: str
    lyrics: str = "[instrumental]"
    duration_s: float
    bpm: int | None = None


@app.get("/health")
async def health() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{COMFYUI_HTTP}/system_stats")
            r.raise_for_status()
    except Exception as e:
        raise HTTPException(502, f"ComfyUI 不可达: {e}") from e
    return {"status": "ok"}


def _build_workflow(req: MusicRequest) -> dict:
    # 每请求现读+深拷贝模板,避免并发请求互相污染共享字典(FastAPI async 单进程内交错执行)。
    wf = copy.deepcopy(_load_workflow())
    wf[NODE_LYRICS]["inputs"]["text"] = req.lyrics
    wf[NODE_STYLE]["inputs"]["text"] = req.prompt
    wf[NODE_DURATION]["inputs"]["value"] = req.duration_s
    wf[NODE_BPM]["inputs"]["value"] = req.bpm or DEFAULT_BPM
    enc = wf[NODE_AUDIO_ENCODE]["inputs"]
    enc["timesignature"] = DEFAULT_TIMESIG
    enc["language"] = DEFAULT_LANGUAGE
    enc["keyscale"] = DEFAULT_KEYSCALE
    return wf


async def _queue_prompt(client: httpx.AsyncClient, wf: dict, client_id: str) -> str:
    r = await client.post(f"{COMFYUI_HTTP}/prompt", json={"prompt": wf, "client_id": client_id})
    r.raise_for_status()
    return r.json()["prompt_id"]


async def _wait_for_completion(ws, prompt_id: str) -> None:
    """在已连接的 WebSocket 上监听本次 prompt_id 的 executing 事件(node=None 即完成)。
    调用方必须在提交 /prompt **之前**就已建立并传入这条连接(见 generate_music)——若反过来
    先提交再连接,ComfyUI 可能在连接建立前就已完成(尤其命中节点缓存时几乎瞬间完成),
    错过事件导致永久挂起直至超时。这是本实现踩过的真实 bug,修复后与 wuzi 的参考脚本
    generate_music_api.py(先 ws.connect 再 queue_prompt)同序。"""
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
        raise HTTPException(504, f"音乐生成超时(>{POLL_TIMEOUT_S}s)") from e
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


def _to_mp3(raw: bytes) -> bytes:
    # ACE-Step SaveAudio 输出格式未必是 mp3(可能 wav/flac);统一转码,与 tts_shim 的
    # "speed 走 ffmpeg atempo 后处理转码为 mp3"同款哲学,下游 MusicClient 只需处理一种格式。
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "raw", Path(td) / "out.mp3"
        src.write_bytes(raw)
        subprocess.run([FFMPEG_BIN, "-y", "-i", str(src), "-c:a", "libmp3lame", "-q:a", "2",
                       str(dst)], check=True, capture_output=True)
        return dst.read_bytes()


@app.post("/v1/audio/music")
async def generate_music(req: MusicRequest) -> Response:
    client_id = str(uuid.uuid4())
    wf = _build_workflow(req)
    uri = f"{COMFYUI_WS}?clientId={client_id}"
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            # 顺序至关重要:先连 WebSocket、连上之后才提交任务,否则 ComfyUI 可能在连接
            # 建立前就已完成(尤其命中节点缓存时近乎瞬间),错过完成事件导致挂到超时。
            async with websockets.connect(uri, open_timeout=10) as ws:
                try:
                    prompt_id = await _queue_prompt(client, wf, client_id)
                except httpx.HTTPError as e:
                    raise HTTPException(502, f"提交 ComfyUI 工作流失败: {e}") from e
                await _wait_for_completion(ws, prompt_id)
        except OSError as e:
            raise HTTPException(502, f"ComfyUI WebSocket 连接失败: {e}") from e
        raw = await _fetch_audio(client, prompt_id)
    mp3 = _to_mp3(raw)
    return Response(content=mp3, media_type="audio/mpeg")
