# 从 GitHub 部署:克隆 → 跑起来

从 `git clone` 起步,在一台全新机器上把 WanderInk 跑起来。

**这份文档管什么、不管什么:**

| 文档 | 管什么 |
|---|---|
| **本文** | 全新机器,从克隆到能打开界面、能登录、能生成 |
| [ops-dgx.md](ops-dgx.md) | 服务起停、查日志、健康检查等**日常运维**(装好之后的事) |
| [deploy-dgx.md](deploy-dgx.md) | 那台 DGX 的**部署变更日志**,含 GB10/sm_121 等硬件坑的唯一记录 |

后两份都假设代码已在机器上,所以它们不回答"怎么装第一次"。本文只讲装第一次;装完的运维**不重复**,直接看上表。

---

## 0. 仓库布局:先搞清楚哪个目录是应用根

代码通过 `git subtree` 从 shanhai 同步到 WanderInk,整个应用被放在 **`web/` 前缀下**。所以克隆之后:

```
WanderInk/                  ← 仓库根(团队总仓:README、samples、comfyui-bridge…)
└── web/                    ← ★ 应用根:后面所有命令都在这里跑
    ├── pyproject.toml
    ├── uv.lock
    ├── src/shanhai/        ← 后端
    ├── web/                ← 前端(是的,web/web,两层)
    ├── assets/             ← 字体与 BGM,已入库
    ├── scripts/
    ├── docs/               ← 本文所在
    └── tests/
```

**后文所有命令,除非特别说明,都在 `WanderInk/web` 下执行。**

> 为什么强调:GitHub 根 README 的「快速开始」里,`cd` 路径与它给的 systemd unit(`WorkingDirectory=%h/shanhai`)对不上,照抄起不来。以本文为准。

运行时会在应用根**读写**这些 cwd 相对路径,所以启动时的工作目录必须是应用根,不能从别处启:

`users.json`(账号)、`config.json`(运行时覆盖)、`projects/`(全部作品数据)、`version.json`(版本戳)。

---

## 1. 前置依赖

| 依赖 | 要求 | 说明 |
|---|---|---|
| Python | **≥ 3.12** | `pyproject.toml` 的 `requires-python` |
| [uv](https://docs.astral.sh/uv/) | 任意近期版本 | 依赖安装与运行统一走它;仓库有 `uv.lock` |
| Node.js | ≥ 18(Vite 5 要求) | 仓库未声明 `engines`;bun 或 npm 二选一 |
| **ffmpeg + ffprobe** | 含 libx264 / aac / libmp3lame | **两个二进制都必须在 PATH 上** |
| git | 任意 | 克隆,以及生成版本戳 |

⚠️ **ffmpeg 是硬依赖且没有预检**:代码里是硬编码的 `"ffmpeg"` / `"ffprobe"`,没有 `shutil.which` 检查。缺失时不会有友好提示,而是在 S5 配音、S6 合成阶段抛 `FileNotFoundError`。装系统包即可(`apt install ffmpeg` / `brew install ffmpeg`),注意某些精简版 ffmpeg 不带 `ffprobe` 或缺编码器。

装完先自检:

```bash
python3 --version && uv --version && node --version && ffmpeg -version | head -1 && ffprobe -version | head -1
```

---

## 2. 最小可跑(接云端端点)

最快跑通的路径:四类上游(文本 / 图像 / 语音 / 音乐)全部接现成的 OpenAI 兼容服务。想用本地 GPU 见 §6。

### 2.1 克隆并安装 Python 依赖

```bash
git clone https://github.com/zhanghui-china/WanderInk.git
cd WanderInk/web
uv sync
```

`uv sync` 会按 `uv.lock` 建 `.venv/` 并装上 dev 组(pytest / ruff / respx)。

### 2.2 配置 `.env`

```bash
cp .env.example .env
```

编辑 `.env`,**至少**填上这两个(它们没有默认值):

```
SHANHAI_BASE_URL=https://your-provider.example.com/v1
SHANHAI_API_KEY=sk-...
```

⚠️ **缺了它们不会在启动时报错**,这一点很坑:服务照常起来、`/api/version` 正常、甚至能登录,但 `GET /api/meta` 返回 **500**(日志里是 pydantic 的 `base_url / api_key Field required`),而前端加载时就要调这个接口 —— 表现为"服务看着好好的,界面却是坏的"。原因是 `api.py` 刻意不在 import 期构造 `Settings()`(那会让缺 `.env` 的环境连进程都起不来),校验被推迟到了第一次真正用它的时候。

生产还要固定这个(不设则每次重启所有人被登出):

```bash
python3 -c "import secrets;print('SHANHAI_SESSION_SECRET='+secrets.token_hex(32))" >> .env
```

变量全集与说明见 `.env.example` 本身,重点几条另见 §4。

### 2.3 跑测试(可选但强烈建议)

```bash
uv run pytest -q
```

全绿说明依赖装对了。这一步不碰网络、不烧额度。

### 2.4 打版本戳 → 构建前端

**顺序不能反**:`version.json` 不入库(它的内容依赖 HEAD,提交它就永远差一个提交),而 Vite 在**编译期**读它把版本烧进页面。

```bash
python3 scripts/stamp-version.py     # 生成 version.json
cd web && npm install && npm run build && cd ..
# 或者:cd web && bun install && bun run build && cd ..
```

产出 `web/dist/`。**不 build 就没有界面** —— 后端是条件挂载:`web/dist` 不存在时只提供 API,访问根路径是 404。

> 构建第一步是 `tsgo -b`(`@typescript/native-preview`,TypeScript 7 原生编译器),不是普通 `tsc`。它由 `npm install` 一并装上,不用单独装。

### 2.5 建第一个账号

**没有自助注册端点**,而且没有 `users.json` 谁也登不进去——所以**第一个管理员账号必须用 CLI 建**:

```bash
uv run shanhai adduser --admin
```

交互式输入用户名与密码,bcrypt 哈希后写入 `users.json`。`--admin` 才能删作品、改全局配置。

之后**后续账号不用再 SSH**:管理员在 Web 的「设置 → 账号」里就能新增用户、重置密码、停用/启用、改管理员标记。CLI 的 `adduser` 仍然可用(适合脚本化),但有个坑:它永远显式传 `--admin` 的真假值,所以拿不带 `--admin` 的 `adduser` 给一个管理员重置密码**会把他降级**;Web 界面上的「重置密码」不会。

### 2.6 启动

```bash
uv run shanhai-web
```

默认 `127.0.0.1:8080`。要让内网其它机器访问,在 `.env` 里设 `SHANHAI_HOST=0.0.0.0`。

打开 http://127.0.0.1:8080 ,用刚建的账号登录。自检:

```bash
curl -s http://127.0.0.1:8080/api/version   # 应返回 build/sha,与 version.json 一致
```

---

## 3. 开发模式(改前端时用)

生产是同源托管(后端托管 `web/dist`);开发时跑两个进程,前端有热更新:

```bash
uv run shanhai-web                 # 后端 :8080
cd web && npm run dev              # 前端 :5173,已代理 /api 与 /files 到 :8080
```

打开 http://localhost:5173 。详见 [../web/README.md](../web/README.md)。

> ⚠️ **必须保持同源部署,不要把前端拆到另一个域名下。** `/files`(成片/页图/配音等产物)
> 要求登录,靠的是浏览器给同源子资源自动带上 session cookie——`<img>`/`<video>`/`<a download>`
> 都是这么过闸的。一旦前后端分域,这些就变成跨源请求:`SHANHAI_CORS_ORIGINS` 默认 `*` 且
> 服务端没有开 `allow_credentials`,cookie 发不出去,**产物会全线 404、页面只剩文字**。
> 开发模式不受影响——Vite 把 `/api` 和 `/files` 一起代理到后端,浏览器视为同源。

---

## 4. 环境变量:几条容易踩的

全集见 `.env.example`(按必填 / 生产必设 / 可选分了三段)。这里只列不看会出事的:

| 变量 | 默认 | 不注意会怎样 |
|---|---|---|
| `SHANHAI_BASE_URL` / `SHANHAI_API_KEY` | **无** | 必填。缺了**不影响启动**,但 `/api/meta` 500、界面打不开(见 §2.2) |
| `SHANHAI_SESSION_SECRET` | 进程内随机 | 不设则**每次重启全员登出**,启动时 stderr 有警告 |
| `SHANHAI_HOST` | `127.0.0.1` | 不改则只有本机能访问 |
| `SHANHAI_SESSION_HTTPS_ONLY` | 关 | ⚠️ **HTTP 直连时绝不能开**,开了 cookie 完全发不出去,表现为"登录成功又立刻要求登录" |
| `SHANHAI_IMAGE_TIMEOUT` | `300` 秒 | 只是**单次 HTTP** 超时。上游排队严重时会 504 导致整页生成失败,需调大 |
| `SHANHAI_FFMPEG_TIMEOUT` | `1800` 秒 | **单条** ffmpeg 命令的墙钟上限,不是整个 S6 的预算。只为防"永久挂住":卡死的 ffmpeg 会占死一个后台作业槽(共 4 个)。实测最贵的一次调用(整片重编码)本机 10 页成片 31s,正常渲染碰不到 |
| `SHANHAI_FFPROBE_TIMEOUT` | `60` 秒 | ffprobe 读时长的上限。实测 0.02s,纯防呆 |
| `SHANHAI_READONLY` | 关 | 开启后关闭新建生成、隐藏编辑入口,适合公网展示实例 |

⚠️ **systemd 场景**:进程环境优先于 `.env`(`load_dotenv(override=False)`)。unit 里必须写 `EnvironmentFile=` 指到 `.env`,不能指望它被自动读取。

---

## 5. 四类模型端点怎么接

代码**只认 OpenAI 兼容协议**,任何符合的服务都能接,四类可以混搭(比如文本走本地 Ollama、图像走云端)。

| 环节 | 用途 | 覆盖变量 |
|---|---|---|
| 文本 LLM | S0 传说检索 / S1 剧本 / S2 分镜 | `SHANHAI_LLM_BASE_URL` `SHANHAI_LLM_API_KEY` `SHANHAI_LLM_MODEL` |
| 图像 | S3 角色三视图 / S4 漫画页 | `SHANHAI_IMAGE_*` + `SHANHAI_IMAGE_API_MODE` |
| 语音 TTS | S5 配音 | `SHANHAI_TTS_*` |
| 音乐 | S5 背景音乐 | `SHANHAI_MUSIC_*` |

**回落链**:某环节没设 `*_BASE_URL` / `*_API_KEY` 就回落到 `SHANHAI_BASE_URL` / `SHANHAI_API_KEY`。所以只用一家服务时,填那一对就够。

`SHANHAI_IMAGE_API_MODE` 要按上游选:`images_api`(`/images/generations`、`/images/edits`)或 `chat_api`(多模态 chat 返图)。选错表现为生图全失败。

装好之后也可以在 Web 界面的配置面板里改(写入 `config.json`,优先级高于 `.env`),不必改文件重启。

**缺某一类会怎样**:TTS 不通 → S5 自动静音兜底,成片完整但没解说;音乐不通 → 无 BGM,状态记 `failed`,不阻断;图像不通 → 该页 `failed`;LLM 不通 → 管线在 S0/S1 就停。

---

## 6. 可选:接本地 ComfyUI(三个 shim)

> 本节只讲「接法概览」。三个 shim 在**空白新机器**上从零部署的完整步骤(依赖、逐个启动、systemd、冒烟、排错)见 [deploy-shims.md](deploy-shims.md)。

想用本地 GPU 出图 / 配音 / 配乐时才需要。**先把三件事说清楚:**

1. **不是必需品。** 上面说过,代码只认 OpenAI 兼容协议,全接云端一样跑通。
2. 仓库里的 `scripts/dgx-shims/<shim>/`(每个含 `main.py` + `pyproject.toml`)是**存档副本,不是线上真源**(见 [scripts/dgx-shims/README.md](../scripts/dgx-shims/README.md))。改仓库这份对线上没有任何效果。
3. 它们**没有各自的依赖清单**,且读取的 ComfyUI 工作流模板是那台机器的绝对路径。**照搬不能开箱即用**,需要你自己适配。

结构是:shim 是薄壳,把 OpenAI 兼容请求翻译成 ComfyUI(默认 `127.0.0.1:8188`)的 websocket 排队协议,再转回 OpenAI 风格响应。

| shim | 端口 | 对应环节 | 接法 |
|---|---|---|---|
| image-shim | 8091 | S3 / S4 | `SHANHAI_IMAGE_BASE_URL=http://127.0.0.1:8091/v1` |
| qwentts-shim | 8090 | S5 配音 | `SHANHAI_TTS_BASE_URL=http://127.0.0.1:8090/v1` |
| music-shim | 8092 | S5 配乐 | `SHANHAI_MUSIC_BASE_URL=http://127.0.0.1:8092/v1` |

⚠️ ComfyUI 队列拥堵时生图会 504、整页失败(日志会打印原因)。此时调大 `SHANHAI_IMAGE_TIMEOUT`,或临时把图像整组切回云端端点。

---

## 7. 生产托管(systemd --user)

以下模板把 `<APP_ROOT>` 换成你的应用根绝对路径(如 `/home/you/WanderInk/web`)。

> GitHub 根 README 里那份 unit 写的是 `WorkingDirectory=%h/shanhai`,那是另一台机器的历史路径,**克隆下来的目录不长那样**,照抄起不来。

`~/.config/systemd/user/wanderink-web.service`:

```ini
[Unit]
Description=WanderInk web (FastAPI + SPA)
After=network-online.target
Wants=network-online.target
RequiresMountsFor=<APP_ROOT>

[Service]
WorkingDirectory=<APP_ROOT>
EnvironmentFile=<APP_ROOT>/.env
Environment="PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=%h/.local/bin/uv run shanhai-web
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now wanderink-web
loginctl enable-linger "$USER"     # 没这条,注销后服务会被杀
```

⚠️ `WorkingDirectory` 必须是应用根 —— `users.json` / `config.json` / `projects/` 都是相对它解析的。

日常起停、查日志、健康检查见 [ops-dgx.md](ops-dgx.md)(服务名换成你自己的即可)。

---

## 8. 升级

```bash
cd WanderInk && git pull
cd web
uv sync                              # 依赖可能有变
python3 scripts/stamp-version.py     # 重新打戳
cd web && npm run build && cd ..     # 重新构建前端
systemctl --user restart wanderink-web
```

**这些文件绝不能被覆盖或删除**(它们都在 `.gitignore` 里,`git pull` 不会动;手工同步代码时要显式排除):

```
.env            config.json      users.json       projects/
```

`projects/` 是全部作品数据(图、音频、成片),没有数据库,丢了就没了。

验证升级生效(比对线上版本与本地 HEAD):

```bash
curl -s http://127.0.0.1:8080/api/version
git rev-parse --short HEAD
```

两者的 sha 一致才算真的部署上了 —— 服务返回 200 只证明它活着,不证明跑的是新代码。

---

## 9. 排错

| 症状 | 原因 | 处理 |
|---|---|---|
| 服务能起、能登录,但界面空白;日志里 `/api/meta` 500 报 `Field required` | `.env` 缺 `SHANHAI_BASE_URL` / `SHANHAI_API_KEY` | 见 §2.2 |
| 启动报端口占用 | 已有实例在跑 | 换 `SHANHAI_PORT`,或停掉旧实例 |
| 打开根路径 404,但 `/api/version` 正常 | 没构建前端,`web/dist` 不存在 | 见 §2.4 |
| 登录成功但立刻又要求登录 | 开了 `SHANHAI_SESSION_HTTPS_ONLY` 却走 HTTP | 关掉它 |
| 重启后所有人被登出 | 没设 `SHANHAI_SESSION_SECRET` | 见 §2.2 |
| 生成到 S5/S6 报 `FileNotFoundError` | 没装 ffmpeg / ffprobe | 见 §1 |
| 成片有画面没解说 | TTS 端点不通,走了静音兜底 | 查 `SHANHAI_TTS_*` 与上游 |
| 某页 `failed`,日志有 504 | 图像上游排队超时 | 调大 `SHANHAI_IMAGE_TIMEOUT`,或切云端端点 |
| 页面版本号显示 `dev` | 没跑 `stamp-version.py` 就构建了 | 见 §2.4 |
| 登不进去,也没有注册入口 | 没建账号 | 见 §2.5 |
