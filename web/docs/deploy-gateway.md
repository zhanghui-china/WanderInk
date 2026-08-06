# shim 网关:给三个 shim 开一个局域网入口

三个 shim(image / qwentts / music)只监听 `127.0.0.1`,只有同机的 shanhai 能调。要让**局域网内其它机器**直接调用生成能力,就在前面加这一层网关。

> **这份文档管什么**:网关本身——部署、路由、配置、systemd、验证、风险。
>
> **不管什么**:三个 shim 的部署(见 [`deploy-shims.md`](deploy-shims.md))、shim 的接口字段与状态码(见 [`shims-api.md`](shims-api.md))、ComfyUI 本体。
>
> 相关文档:参考机(DGX)的历史变更见 [`deploy-dgx.md`](deploy-dgx.md);日常运维速查见 [`ops-dgx.md`](ops-dgx.md);源码存档说明见 [`../scripts/dgx-shims/README.md`](../scripts/dgx-shims/README.md)。

---

## 0. 先读这一条:shanhai 自己**不要**走网关

**shanhai 应用 `.env` 里的三行 `SHANHAI_*_BASE_URL` 必须保持 `http://127.0.0.1:809x/v1`。填网关地址会静默破坏两处保护:**

| 代码 | base_url 是 loopback | 改成网关的局域网地址后 |
|---|---|---|
| `src/shanhai/providers/_http.py` 的 `local_backend_guard()` | 全局单并发锁,同一时刻只有一个请求打 GPU | **锁直接失效**——`_LOCAL_HOSTS` 只认 `127.0.0.1` / `localhost` / `::1`。2026-07-13 实测:并发命中同卡时 LLM 从数十秒被拖到接近 900s 超时 |
| `src/shanhai/runtime_config.py` 的 `image_concurrency()` | 返回 `1`(串行出图) | 返回 `REMOTE_IMAGE_CONCURRENCY = 2`,S3/S4 自动变 2 路并发打同一张卡 |

两处都按 **hostname** 判定,改错了不会报错、不会有日志,只会在生成时表现为莫名其妙的超时。

**网关只服务外部调用方。shanhai 继续直连 loopback。** 这也带来一个好处:网关挂了,shanhai 完全不受影响。

> 顺带一提:Web 配置面板的**按用户**那一层(`config.json` 的 `users`)**只开放 LLM 五个字段**,普通用户改不了 image 端点——`runtime_config.UserOverride` 的 `extra="forbid"` 会直接 422。这是刻意做成结构约束而不是纪律要求:上表两条一旦被某个用户从个人配置里绕开,后果和填错 `.env` 完全一样,而且更难查(每个人的配置还不一样)。能改 image 端点的仍然只有管理员的 `global` / `stages` 两层,也就是本节警告覆盖的范围。

---

## 1. 架构

```
局域网其它机器 / 你的笔记本
   │  OpenAI 兼容 HTTP
   ▼
┌──────────────────────────────┐
│  gateway  0.0.0.0:8099       │   ← 本文部署的进程,唯一对外的面
└───┬──────────┬──────────┬────┘
    │          │          │       全部 127.0.0.1,不对外
    ▼          ▼          ▼
 image      qwentts     music        ← 三个 shim,不改动
 :8091      :8090       :8092
    └──────────┴──────────┘
               ▼
        ComfyUI 127.0.0.1:8188

shanhai (S3/S4/S5) ──直连──▶ 三个 shim(**不经过网关**,见 §0)
```

网关自己不做任何业务逻辑:按路由表把**原始字节**搬给上游,再把上游的字节原样搬回来。

**为什么是"加一层"而不是"把三个 shim 合成一个进程"**:qwentts 和 music 把同步的 `subprocess.run(ffmpeg)` 直接写在 `async` 处理函数里,合进一个进程后一次转码会冻住全部路由;而且重启粒度会从"一个 shim"变成"全部",正在跑的生成会被一起打断。

---

## 2. 路由表

路径**原样保留**,不加 `/image`、`/tts` 之类前缀。所以调用方 base_url 统一是 `http://<LAN-IP>:8099/v1`,一个地址同时供三种能力,且保持 OpenAI 兼容(现成的 OpenAI SDK 指过来就能用)。

| 方法 | 路径 | 转发到 | 请求体 | 响应 |
|---|---|---|---|---|
| POST | `/v1/images/generations` | image :8091 | JSON | JSON(`b64_json`) |
| POST | `/v1/images/edits` | image :8091 | multipart | JSON(`b64_json`) |
| POST | `/v1/audio/speech` | qwentts :8090 | JSON | 原始 mp3 |
| POST | `/v1/voices/clone` | qwentts :8090 | multipart | JSON |
| GET | `/v1/models` | qwentts :8090 | — | JSON |
| POST | `/v1/audio/music` | music :8092 | JSON | 原始 mp3 |
| GET | `/health` | **网关自己实现** | — | 聚合三个上游 |

字段细节见 [`shims-api.md`](shims-api.md),网关不改写任何请求或响应。

**三条必须知道的规则:**

1. **`/v1/audio/` 这一层是分裂的**:`speech` 归 qwentts、`music` 归 music。任何按 `/v1/audio/` 粗分流的做法都会分错。
2. **表里没有的路径一律 404**,网关不盲目转发。这挡掉了三个 shim 各自自带的 `/docs`、`/redoc`、`/openapi.json`(FastAPI 默认路由,无鉴权且会泄漏内部接口结构),网关自己的那几个也已关闭。
3. **shim 新增路由必须同步网关的 `_ROUTES` 表**,否则新路由在网关上静默 404。

### `/health`(网关的聚合探针)

三个 shim 各有各的 `/health`,是唯一真冲突,收在网关这一处:

```bash
curl http://<LAN-IP>:8099/health
```

```json
{"ok": false,
 "upstreams": {"image": {"ok": true},
               "tts":   {"ok": true},
               "music": {"ok": false, "status_code": 502,
                         "upstream": {"detail": "ComfyUI 不可达: All connection attempts failed"}}}}
```

三个都通 → 200,任一不通 → 503,且 body 里指名道姓是哪一个、上游原话是什么。

> 注意 image-shim 的 `/health` 在 ComfyUI 异常时返回的是 **200 + `{"ok": false}`**(不是错误码)。网关按 `ok` 字段判定,不会被这个状态码骗过去。

---

## 3. 部署

前置:三个 shim 已按 [`deploy-shims.md`](deploy-shims.md) 部署完毕且 active。

```bash
# 1. 从仓库拷一份到 home(和三个 shim 同样的做法)
cp -r <仓库>/scripts/dgx-shims/gateway ~/gateway
cd ~/gateway

# 2. 建环境(只有 fastapi / uvicorn / httpx 三个依赖)
uv sync

# 3. 前台先跑一次确认能起来
uv run uvicorn main:app --host 0.0.0.0 --port 8099
curl -s http://127.0.0.1:8099/health        # 三个上游都通才是 {"ok": true, ...}
```

`0.0.0.0` 是**刻意**的——与三个 shim 不同,网关就是要对局域网可见。

**部署前先确认 8099 没被占**(DGX 是共享机):

```bash
ss -ltn | grep 8099        # 应无输出
```

---

## 4. 配置

三个上游地址与超时全部走环境变量,**默认值就是三个 loopback 地址**,所以正常部署一个都不用设。

| 变量 | 默认 | 说明 |
|---|---|---|
| `GATEWAY_IMAGE_URL` | `http://127.0.0.1:8091` | image-shim |
| `GATEWAY_TTS_URL` | `http://127.0.0.1:8090` | qwentts-shim |
| `GATEWAY_MUSIC_URL` | `http://127.0.0.1:8092` | music-shim |
| `GATEWAY_TIMEOUT_IMAGE_S` | `600` | 须 > image 自己的墙钟上限 |
| `GATEWAY_TIMEOUT_TTS_S` | `420` | 须 > qwentts 的 |
| `GATEWAY_TIMEOUT_MUSIC_S` | `660` | 须 > music 的 |

**超时为什么这么宽**——网关必须比上游更能等,否则网关先断,调用方拿到的是网关那句没信息量的 504,而 ComfyUI 里的任务**还在跑**,GPU 白烧:

| 上游 | 自身上限 | 网关给 |
|---|---|---|
| image | 轮询 240s + 最多 3 张参考图上传(各 60s)+ 取图 60s ≈ 最坏 480s | 600s |
| qwentts | 轮询 180s + ws/http 约 70s + 克隆路径一次**无超时**的 ffmpeg 变速 | 420s |
| music | 轮询 300s + 约 70s + 每次都跑的**无超时** ffmpeg 转码 | 660s |

⚠️ 哪天有人调大了 shim 的 `QWENTTS_SHIM_POLL_TIMEOUT_S` / `MUSIC_SHIM_POLL_TIMEOUT_S`,**这里要跟着调大**。

---

## 5. systemd 托管(user 服务)

`~/.config/systemd/user/shanhai-gateway.service`:

```ini
[Unit]
Description=shanhai shim gateway (LAN entry)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=%h/gateway
# ⚠️ --host 0.0.0.0:与三个 shim 不同,网关刻意绑全部网卡、对局域网可见。
#    安全边界因此从"只监听 127.0.0.1"变成了纯网络层,见 §8。
ExecStart=%h/gateway/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8099
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now shanhai-gateway
# loginctl enable-linger 若已开过就不用重复
```

两点说明:

- **上游地址走默认值,unit 里不需要任何 `Environment=`**。要改上游或超时才追加。
- **刻意不加 `After=shanhai-image.service`**。网关不需要上游活着就能启动(只在转发那一刻才连)。三个 shim 挂着,网关照样起来并返回带具体原因的 502——那正是想要的可观测行为。

---

## 6. 外部调用示例

把 `<LAN-IP>` 换成 DGX 的局域网 IP(`hostname -I` 现查,走 DHCP 会变)。

```bash
G=http://<LAN-IP>:8099

# 文生图
curl -s -X POST $G/v1/images/generations \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"青山云雾,水墨","size":"1536x1024"}' | head -c 200

# 参考图编辑(最多 3 张,第 4 张起被 image-shim 静默丢弃)
curl -s -X POST $G/v1/images/edits \
  -F "prompt=让他站在山门前" -F "size=1536x1024" -F "lora=figurine_qwen" \
  -F "image[]=@ref1.png" -F "image[]=@ref2.png" -o out.json

# 配音(直接落 mp3)
curl -s -X POST $G/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"input":"很久以前,雷峰塔下……","voice":"女声","speed":1.0}' -o line.mp3

# 配乐(prompt 是风格标签,不是歌词;duration_s 必填)
curl -s -X POST $G/v1/audio/music \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"guzheng, calm, cinematic","duration_s":30}' -o bgm.mp3
```

用 OpenAI SDK 时 base_url 填 `http://<LAN-IP>:8099/v1` 即可,api_key 随便填(不校验)。

---

## 7. 排错

| 症状 | 原因 | 处理 |
|---|---|---|
| 网关返回 `{"detail":"网关无法连接上游 <名字>(...)"}`,**502** | 那个 shim 进程没起 | `systemctl --user status shanhai-<image\|tts\|music>` |
| 网关返回 `{"detail":"上游 <名字> 超时(<N>s)"}`,**504** | 上游卡住超过网关的超时 | 先看 ComfyUI 队列(`curl :8188/queue`);确认不是 §4 的超时配小了 |
| 返回 `{"detail":"网关未注册此路由: ..."}`,**404** | 路径/方法不在 §2 表里 | 核对表;若是 shim 新增的路由,要同步网关的 `_ROUTES` |
| `/health` 返回 503 | body 里已指明哪个上游、上游原话是什么 | 按名字去查那个 shim |
| 其它 4xx/5xx 且 detail 里**没有**"网关"二字 | 是上游 shim 自己的错误,网关原样透传 | 按 [`shims-api.md`](shims-api.md) §6 的错误码表查 |
| 服务 active、`curl 127.0.0.1:8099` 通,但**别的机器连不上** | 机器防火墙。user 服务没有 sudo,改不了 iptables | 找有 sudo 的人放行 8099,或确认网段策略 |
| `address already in use` | 8099 被占(共享机) | `ss -ltnp \| grep 8099` 看是谁;换端口要同时改 unit |

区分是网关的错还是 shim 的错:**网关自己造的错误码只有 502(连不上)、504(超时)、404(路由没注册),且 detail 里一定有"网关"二字**。其余全是上游原话。

---

## 8. 风险与边界

**这一节不是免责声明,是运维时真会遇到的东西。**

### 8.1 无鉴权:局域网内任何人都能驱动这块 GPU

网关不校验任何 token,安全边界**完全落在网络层**。同网段的任何人都能提交生成任务。而 ComfyUI 进程归队友 `wuzi` 维护、`huntun` 账号既没 sudo 也无权管它,真被占满只能求人。

另外 `/v1/images/edits` 和 `/v1/voices/clone` 是**文件上传**入口,会往 ComfyUI 的 `input/` 里写文件,而那边**没有清理机制**(见 `shims-api.md` §7)——理论上是一条磁盘填满向量,在共享机上会波及别人的工作。

> 后续想加 token 的话成本很低:shanhai 的三个 provider 本来就在每个请求上发 `Authorization: Bearer <api_key>`,网关加一行校验即可,调用方零改动。

### 8.2 外部调用会和 shanhai 抢 ComfyUI 队列

§0 那把单并发锁是 **shanhai 进程内的锁**,管不到别的进程。经网关进来的请求直接排进 ComfyUI 队列,和 shanhai 的 S3/S4/S5 流水线抢同一块卡。

具体会怎么坏:shanhai 的 `ImageClient` 超时 300s、S4 单格预算 600s。队列里多几个外部长任务,shanhai 那一页就等不到 → 超时 → **且这类请求判定为非幂等、不重试** → 该页直接失败。而用户看到的只是"生成失败",没有任何线索指向"有人在用网关"。

这是**把能力开放出去本身的代价**,不是网关设计缺陷。缓解只能靠约定:网关面向低频/人工调用,不要拿它去驱动另一条流水线。真要长期并存,需要把队列管理上移成独立组件——超出本文范围。

排查线索:uvicorn 自带的访问日志里有调用方 IP,`journalctl --user -u shanhai-gateway` 能看到是谁在调。

### 8.3 "局域网"不是一个稳定的边界

DGX 上有 cpolar 隧道。**只要有人把 8099 加进 cpolar 转发,这套东西就上公网了**,而且网关自己察觉不到。cpolar 只应暴露主站端口,不要转 8099。同理,机器换网段或接入访客 WiFi 时,"局域网"的实际人群会变。

### 8.4 多一个进程 = 多一个单点

网关挂了,三个能力对外全断。**但 shanhai 完全不受影响**(它直连 loopback),所以这是 P2 而不是 P0。`Restart=always` 已覆盖进程崩溃。

### 8.5 存档副本会漂移

和三个 shim 一样:仓库里 `scripts/dgx-shims/gateway/` 是**副本**,线上真源是 `~/gateway/`,改仓库不会生效。改完必须 scp 过去并重启。
