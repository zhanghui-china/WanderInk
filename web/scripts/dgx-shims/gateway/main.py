"""shim 网关:把三个只绑 loopback 的 shim 合并成一个局域网入口。

三个 shim(image :8091 / qwentts :8090 / music :8092)自己无鉴权,一直靠只监听
127.0.0.1 兜底。要让局域网内其它机器调用生成能力,就在前面加这一层:网关绑
0.0.0.0,三个 shim 保持原样不动,网关是唯一对外的面。

**不合并成一个进程**是刻意的:qwentts/music 把同步 subprocess.run(ffmpeg) 直接写在
async 处理函数里,合进一个进程后一次转码会冻住全部路由;而且重启粒度会从"一个 shim"
变成"全部",正在跑的生成会被一起打断。

⚠️ shanhai 自己**不要**改成走这里。src/shanhai/providers/_http.py 的
local_backend_guard() 与 runtime_config.py 的 image_concurrency() 都按 hostname 判定:
base_url 一旦不是 127.0.0.1/localhost,同卡串行保护会静默失效、图像并发会自动变 2。
"""
import asyncio
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

# 上游地址与各自的读超时。默认全是 loopback——网关和三个 shim 同机是唯一支持的形态。
# 超时必须大于 shim 自己的墙钟上限,否则网关先断,调用方看到的是网关的 504 而不是
# shim 的真实错误。image: 轮询 240s + 最多 3 次参考图上传;tts: 180s + 无超时的
# ffmpeg 变速;music: 300s + 无超时的 ffmpeg 转码(所以它给得最宽)。
_UPSTREAMS = {
    "image": (os.getenv("GATEWAY_IMAGE_URL", "http://127.0.0.1:8091"),
              float(os.getenv("GATEWAY_TIMEOUT_IMAGE_S", "600"))),
    "tts": (os.getenv("GATEWAY_TTS_URL", "http://127.0.0.1:8090"),
            float(os.getenv("GATEWAY_TIMEOUT_TTS_S", "420"))),
    "music": (os.getenv("GATEWAY_MUSIC_URL", "http://127.0.0.1:8092"),
              float(os.getenv("GATEWAY_TIMEOUT_MUSIC_S", "660"))),
}

# 路径原样保留(不加 /image、/tts 之类前缀),调用方 base_url 统一是
# http://<LAN-IP>:8099/v1,一个地址同时供三种能力且保持 OpenAI 兼容。
# ⚠️ /v1/audio/ 这一层是分裂的:speech 归 qwentts、music 归 music,只能精确匹配。
# ⚠️ shim 新增路由必须同步这张表,否则新路由在网关上静默 404。
_ROUTES = {
    ("POST", "/v1/images/generations"): "image",
    ("POST", "/v1/images/edits"): "image",
    ("POST", "/v1/audio/speech"): "tts",
    ("POST", "/v1/voices/clone"): "tts",
    ("GET", "/v1/models"): "tts",
    ("POST", "/v1/audio/music"): "music",
}

# 逐跳首部不能转发;host 由 httpx 按上游地址重设,content-length 按实际 body 重算,
# 原样带上都会冲突。剩下的(含 authorization、content-type)一律原样透传——
# content-type 里就带着 multipart 的 boundary,是下面"不解析"能成立的前提。
_DROP_REQUEST_HEADERS = {"host", "content-length", "connection", "keep-alive",
                         "transfer-encoding", "upgrade", "te", "trailer"}

_clients: dict[str, httpx.AsyncClient] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """每个上游一个长生命周期 AsyncClient:复用连接,也避免每请求建客户端时
    "客户端先于响应体关闭"那类坑。"""
    for name, (base_url, read_timeout) in _UPSTREAMS.items():
        _clients[name] = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(read_timeout, connect=10.0),
        )
    try:
        yield
    finally:
        await asyncio.gather(*(c.aclose() for c in _clients.values()))
        _clients.clear()


# 网关是对外的面,不该给局域网做接口自述,故关掉自带的 /docs /redoc /openapi.json;
# 三个 shim 自己那几份(FastAPI 默认带的)也不在 _ROUTES 里,一律 404。
app = FastAPI(title="shanhai-shim-gateway", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)


async def _probe(name: str) -> dict:
    """探一个上游的 /health 并归一化——三个 shim 的健康响应形状不一样:
    image 返回 {"ok": bool}(注意 ComfyUI 异常时它仍是 200 而 ok=false),
    qwentts/music 返回 {"status": "ok"},失败则 502。"""
    try:
        r = await _clients[name].get("/health", timeout=15.0)
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)}
    try:
        body = r.json()
    except ValueError:
        body = {}
    ok = r.status_code == 200 and body.get("ok", True) is not False
    out: dict = {"ok": ok}
    if not ok:
        out["status_code"] = r.status_code
        out["upstream"] = body
    return out


@app.get("/health")
async def health():
    """聚合探针:三个上游都通才 200。三个 shim 的 /health 各自重名,统一收在这里。"""
    names = list(_UPSTREAMS)
    results = await asyncio.gather(*(_probe(n) for n in names))
    upstreams = dict(zip(names, results))
    ok = all(u["ok"] for u in upstreams.values())
    return JSONResponse({"ok": ok, "upstreams": upstreams},
                        status_code=200 if ok else 503)


@app.api_route("/{full_path:path}", methods=["GET", "POST"])
async def forward(full_path: str, request: Request):
    path = request.url.path
    name = _ROUTES.get((request.method, path))
    if name is None:
        raise HTTPException(404, f"网关未注册此路由: {request.method} {path}")

    # 原始字节直接转发,**不解析 multipart**:解析再重组会丢 boundary、丢 image[]
    # 的重复字段语义(image-shim 靠 getlist 取前 3 张),所以网关连
    # python-multipart 都不需要装。
    body = await request.body()
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in _DROP_REQUEST_HEADERS}
    target = path if not request.url.query else f"{path}?{request.url.query}"

    try:
        r = await _clients[name].request(request.method, target,
                                         content=body, headers=headers)
    except httpx.TimeoutException as e:
        raise HTTPException(504, f"上游 {name} 超时({_UPSTREAMS[name][1]:.0f}s): {e}") from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"网关无法连接上游 {name}({_UPSTREAMS[name][0]}): {e}") from e

    # 上游状态码与响应体原样回传,包括 image /health 那种"200 但 ok=false"的语义。
    # 网关只在自己出问题时才造状态码(上面的 502/504)。
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type"))
