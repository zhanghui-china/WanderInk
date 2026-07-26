"""ComfyUI 图像生成中转层(shim)。

对外暴露 OpenAI 兼容的 images_api 契约(/images/generations + /images/edits),
供 shanhai 的 ImageClient(mode="images_api")直接调用;内部把请求转成 ComfyUI
原生 HTTP API 调用(POST /prompt -> 轮询 GET /history/{id} -> GET /view,
需要参考图时先 POST /upload/image)。

设计依据见 ~/.claude/plans/plan-floofy-lantern.md(shanhai 项目仓库外的实施计划)。
DGX 专属运维脚本,不纳入 shanhai git 仓库版本控制(与 tts_shim.py 现状一致)。
"""
import asyncio
import base64
import json
import random
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

COMFYUI_SERVER = "http://127.0.0.1:8188"
COMFYUI_ROOT = Path("/home1/wuzi/WanderInk/comfyui-bridge")  # 只读:读队友维护的工作流模板,不写入他的目录树
POLL_TIMEOUT_S = 240  # 共享 GPU,可能排队;shanhai 侧 ImageClient 给了 300s 总预算
POLL_INTERVAL_S = 2

# 参考图数量 -> 工作流模板文件 + 节点号(已对照 image_edit/blend/triple_blend_workflow.json
# 原始内容逐一核实:三套编辑工作流均在节点 68 承载 prompt、节点 126 承载 aspect_ratio;
# 图片加载节点依次是 41 / 41+79 / 41+79+133,不可跨工作流混用同一套节点号表)
# lora_node:承载 LoRA 的 LoraLoaderModelOnly 节点号。**单/双图是 133,三图是 135**——
# 三图工作流里 133 是第三张参考图的加载节点(见同行 image_nodes),写死 133 会把参考图冲掉。
_EDIT_WORKFLOWS = {
    1: {"file": "image_edit_workflow.json", "image_nodes": ["41"], "lora_node": "133"},
    2: {"file": "image_blend_workflow.json", "image_nodes": ["41", "79"], "lora_node": "133"},
    3: {"file": "image_triple_blend_workflow.json", "image_nodes": ["41", "79", "133"],
        "lora_node": "135"},
}

# LoRA 短名 -> 实际 safetensors 文件名。取值与队友 comfyui_*_service 的 LORA_MAPPING 一致,
# 两边必须同一套词汇;key 全小写 + 查表前 .lower(),故对调用方大小写不敏感。
_LORA_MAPPING = {
    "real_ani_qwen": "Real_Ani-Qwen_000001250.safetensors",
    "figurine_qwen": "figurine_qwen.safetensors",
    "bjd.7arl": "bjdE5A883E5A883V2004.7ARL.safetensors",
}
# 工作流模板里 LoRA 节点是焊死存在的,不存在"不用 LoRA":不传、或传了不认识的值,都回落这个
# 默认(与队友服务行为一致)——宁可风格不是想要的,也不该因为一个可选参数让整轮生成失败。
_LORA_DEFAULT = "Real_Ani-Qwen_000001250.safetensors"


def _lora_filename(lora: str | None) -> str:
    if not lora:
        return _LORA_DEFAULT
    name = lora.strip()
    if name.endswith(".safetensors"):   # 调用方直接给文件名时原样透传
        return name
    return _LORA_MAPPING.get(name.lower(), _LORA_DEFAULT)


_TEXT2IMG_FILE = "Text2IMGKrea2_api.json"
_TEXT2IMG_PROMPT_NODE, _TEXT2IMG_PROMPT_FIELD = "51", "text"
_TEXT2IMG_RATIO_NODE = "49"
_EDIT_PROMPT_NODE, _EDIT_PROMPT_FIELD = "68", "prompt"
_EDIT_RATIO_NODE = "126"

# ResolutionSelector 节点(comfy_extras/nodes_resolution.py::AspectRatio)接受的唯一 8 个取值,
# 附对应宽高比数值,供 size(如 "1536x1024")到这套字符串的最近匹配。
_ASPECT_RATIOS: list[tuple[str, float]] = [
    ("1:1 (Square)", 1.0),
    ("4:3 (Standard)", 4 / 3),
    ("3:4 (Portrait Standard)", 3 / 4),
    ("3:2 (Photo)", 3 / 2),
    ("2:3 (Portrait Photo)", 2 / 3),
    ("16:9 (Widescreen)", 16 / 9),
    ("9:16 (Portrait Widescreen)", 9 / 16),
    ("21:9 (Ultrawide)", 21 / 9),
]


def size_to_aspect_ratio(size: str) -> str:
    """把 "1536x1024" 这类不透明字符串映射到 ResolutionSelector 的 8 个合法取值之一。
    不追求精确匹配:PAGE_TMPL 已在文字里显式要求 16:9,下游 typeset.compose_page 又做
    cover-crop 兜底,这里只需方向(横/竖/方)基本对即可。"""
    try:
        w, h = size.lower().split("x")
        ratio = int(w) / int(h)
    except (ValueError, ZeroDivisionError):
        return "16:9 (Widescreen)"  # 解析失败时退回 shanhai 的常见画幅
    return min(_ASPECT_RATIOS, key=lambda pair: abs(pair[1] - ratio))[0]


def _randomize_seeds(workflow: dict) -> dict:
    """把工作流里所有写死的 seed 换成随机值。

    ComfyUI 对"输入完全相同"的节点有执行缓存,而模板里的 seed 是常量——于是同一个
    提示词永远拿回上一次那份产物,"重绘"从机制上就不可能出新结果(2026-07-26 实测:
    同一请求连发两次返回的图字节完全相同,第二次 0 秒返回)。

    遍历全部节点而不是写死节点号:模板改版或新增采样器都不会漏。值是 link(如
    ["65", 0])的跳过——那是连线不是常量。"""
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if isinstance(inputs, dict) and isinstance(inputs.get("seed"), int):
            inputs["seed"] = random.randrange(2**53)
    return workflow


def _load_workflow(filename: str) -> dict:
    path = COMFYUI_ROOT / filename
    if not path.exists():
        raise HTTPException(500, f"工作流模板缺失: {path}(队友 ComfyUI 目录结构是否变动?)")
    return _randomize_seeds(json.loads(path.read_text(encoding="utf-8")))


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=COMFYUI_SERVER, timeout=60)


async def _queue_prompt(client: httpx.AsyncClient, workflow: dict) -> str:
    r = await client.post("/prompt", json={"prompt": workflow, "client_id": "shanhai-image-shim"})
    if r.status_code != 200:
        raise HTTPException(502, f"ComfyUI 拒绝提交任务({r.status_code}): {r.text[:300]}")
    return r.json()["prompt_id"]


async def _wait_and_fetch(client: httpx.AsyncClient, prompt_id: str) -> bytes:
    """纯 HTTP 轮询 /history(不用 WebSocket),贴合 shim 对外承诺的同步语义。"""
    deadline = time.monotonic() + POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        r = await client.get(f"/history/{prompt_id}")
        r.raise_for_status()
        hist = r.json()
        if prompt_id in hist:
            outputs = hist[prompt_id]["outputs"]
            for node_out in outputs.values():
                if "images" in node_out and node_out["images"]:
                    img = node_out["images"][0]
                    v = await client.get("/view", params={
                        "filename": img["filename"], "subfolder": img["subfolder"],
                        "type": img["type"]})
                    v.raise_for_status()
                    return v.content
            raise HTTPException(502, f"ComfyUI 执行完成但无图像输出: {json.dumps(outputs)[:300]}")
        await asyncio.sleep(POLL_INTERVAL_S)
    raise HTTPException(504, f"等待 ComfyUI 渲染超时({POLL_TIMEOUT_S}s): prompt_id={prompt_id}")


async def _upload_reference(client: httpx.AsyncClient, data: bytes) -> str:
    """纯 HTTP 上传到 ComfyUI input/(不需要 wuzi 目录写权限,进程自己写)。
    文件名加前缀+uuid,避免和队友素材撞名/便于将来按前缀识别清理(已知限制:ComfyUI
    无标准删除接口,这些临时参考图会持续累积在 input/,本次不做自动清理)。"""
    filename = f"shanhai_ref_{uuid.uuid4().hex[:12]}.png"
    r = await client.post("/upload/image", files={"image": (filename, data, "image/png")},
                          data={"overwrite": "true"})
    r.raise_for_status()
    return r.json()["name"]


async def _generate(prompt: str, size: str, references: list[bytes],
                    lora: str | None = None) -> bytes:
    n = len(references)
    async with _client() as client:
        if n == 0:
            workflow = _load_workflow(_TEXT2IMG_FILE)
            workflow[_TEXT2IMG_PROMPT_NODE]["inputs"][_TEXT2IMG_PROMPT_FIELD] = prompt
            workflow[_TEXT2IMG_RATIO_NODE]["inputs"]["aspect_ratio"] = size_to_aspect_ratio(size)
            # 文生图模板(Text2IMGKrea2)里没有 LoRA 节点,lora 在这条路径上无处可用——
            # 静默忽略而非报错:S3 角色三视图走的就是这条路,不该因为选了 LoRA 就整体失败。
        else:
            # 4+ 张只取前 3 张:队友未提供四图工作流,第 4+ 角色仅靠 prompt 文字里的
            # feature_prompt 描述兜底(与 S3 里 MAX_TURNAROUND 之外角色的降级逻辑同一套哲学)。
            used = references[:3]
            spec = _EDIT_WORKFLOWS[len(used)]
            workflow = _load_workflow(spec["file"])
            for node, ref_bytes in zip(spec["image_nodes"], used):
                filename = await _upload_reference(client, ref_bytes)
                workflow[node]["inputs"]["image"] = filename
            workflow[_EDIT_PROMPT_NODE]["inputs"][_EDIT_PROMPT_FIELD] = prompt
            workflow[_EDIT_RATIO_NODE]["inputs"]["aspect_ratio"] = size_to_aspect_ratio(size)
            workflow[spec["lora_node"]]["inputs"]["lora_name"] = _lora_filename(lora)
        prompt_id = await _queue_prompt(client, workflow)  # 每次都是新读的字典,无需 deepcopy
        return await _wait_and_fetch(client, prompt_id)


app = FastAPI(title="shanhai-image-shim")


@app.get("/health")
async def health():
    try:
        async with _client() as client:
            r = await client.get("/system_stats", timeout=10)
        return {"ok": r.status_code == 200}
    except httpx.HTTPError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)


@app.post("/v1/images/generations")
async def images_generations(request: Request):
    body = await request.json()
    prompt = body.get("prompt", "")
    size = body.get("size", "1536x1024")
    if not prompt.strip():
        raise HTTPException(400, "prompt 不能为空")
    img_bytes = await _generate(prompt, size, references=[], lora=body.get("lora"))
    return {"data": [{"b64_json": base64.b64encode(img_bytes).decode()}]}


@app.post("/v1/images/edits")
async def images_edits(request: Request):
    form = await request.form()
    prompt = str(form.get("prompt", ""))
    size = str(form.get("size", "1536x1024"))
    if not prompt.strip():
        raise HTTPException(400, "prompt 不能为空")
    files = form.getlist("image[]")
    if not files:
        raise HTTPException(400, "images/edits 需要至少一个 image[] 文件")
    references = [await f.read() for f in files]
    lora = form.get("lora")
    img_bytes = await _generate(prompt, size, references=references,
                                lora=str(lora) if lora else None)
    return {"data": [{"b64_json": base64.b64encode(img_bytes).decode()}]}
