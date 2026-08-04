# DGX shim 存档(副本,不是真源)

> ⚠️ **改这里的文件不会有任何效果。** 线上跑的是 DGX 上 `~/<shim名>/main.py`,
> 这里只是一份留档副本,便于查阅、code review 与机器重装时恢复。改完必须手动同步过去
> 并重启服务(见下方"同步回 DGX")。

## 这些是什么

shanhai 通过 OpenAI 兼容接口调用三个本地服务,它们各自是"薄壳"(shim):
自己不做任何生成计算,只把请求翻译成 ComfyUI 的排队协议,再把结果转回 OpenAI 风格响应。
真正的算力在 ComfyUI(`127.0.0.1:8188`),那个进程由另一位队友(系统用户 `wuzi`)维护,
`huntun` 账号无权管理它。

每个 shim 现在是一个自包含的 uv 项目子目录(`main.py` + `pyproject.toml`),
空白新机器上 `cd` 进去 `uv sync` 就能建好环境——完整部署见
[`../../docs/deploy-shims.md`](../../docs/deploy-shims.md),
对外 HTTP 接口(字段/状态码/示例)见 [`../../docs/shims-api.md`](../../docs/shims-api.md)。

| 存档目录 | 线上位置 | 端口 | 用途 |
|---|---|---|---|
| `image-shim/` | `~/image-shim/main.py` | 8091 | 图像生成/编辑(S3 三视图、S4 漫画页) |
| `qwentts-shim/` | `~/qwentts-shim/main.py` | 8090 | 语音合成(S5 配音,Qwen3-TTS VoiceDesign) |
| `music-shim/` | `~/music-shim/main.py` | 8092 | 背景音乐(S5 BGM,ACE-Step) |
| `gateway/` | `~/gateway/main.py` | 8099 | **可选**。三个 shim 的统一局域网入口,见 [`../../docs/deploy-gateway.md`](../../docs/deploy-gateway.md) |

`gateway/` **不是** shim——它不做协议转换、不碰 ComfyUI,只按路由表原样转发字节。
上面三个只绑 `127.0.0.1`,只有它绑 `0.0.0.0`。
⚠️ **给某个 shim 加了新路由,必须同步 `gateway/main.py` 的 `_ROUTES` 表**,否则新路由经网关是静默 404。

三者都读 `wuzi` 维护的工作流模板(`/home1/wuzi/WanderInk/comfyui-bridge/*.json`),
**只读、不写入他的目录树**——所以模板一旦改版/搬家,shim 里的节点号或路径可能失配
(2026-07-14 就因为模板搬家导致图像生成全面故障过一次,见 `docs/deploy-dgx.md`)。

## 为什么会有这份存档

这三个文件长期游离在版本控制之外,直接后果是:改动无人 review、出问题无从对比、
机器重装就得从头再写。2026-07-26 修完两个隐蔽 bug 后补上这份存档。

### 存档时(2026-07-26)包含的关键修复

**1. seed 随机化(三个 shim 都有)** —— `_randomize_seeds()`

ComfyUI 对"输入完全相同"的节点有执行缓存,而工作流模板里的 `seed` 是写死的常量。
两者叠加的后果是:**同一段文本/同一个提示词永远拿回上一次那份产物**,"重绘"/"重配音"
从机制上就不可能产出新结果。实测坐实过:同一请求连发两次返回字节完全相同、第二次 0 秒返回;
换个文案则要 11 秒真跑。这也让 `s5_audio.py` 里那套"三试取最长"的截断重试彻底空转
(三次拿回同一份缓存)。

实现上**遍历全部节点**而不是写死节点号——模板改版或新增采样器都不会漏;
值是连线形式(如 `["65", 0]`)的跳过,那不是常量。

副作用(是正确行为,不是变慢):以前同提示词 2.1 秒返回是因为吃缓存等于没生成,
现在每次都真跑约 28 秒。S4 逐页生成不受影响(每页提示词本就不同、从未命中缓存),
但**单页重绘从"瞬间"变成约 28 秒**。

**2. LoRA 透传(image-shim)** —— `_LORA_MAPPING` / `_lora_filename()`

此前 shanhai 发的 LoRA 参数被 shim 完全忽略,配置面板选什么都没用,生图一直走模板里
写死的默认 LoRA。现按队友 `comfyui_*_service` 的契约接收 `lora` 短名
(`Real_ani_qwen` / `figurine_qwen` / `bjd.7ARL`,大小写不敏感),映射成 safetensors 文件名。

⚠️ **LoRA 节点号不固定**:单图/双图工作流是 **133**,三图工作流里 133 是第三张参考图的
加载节点、LoRA 节点是 **135**。写死 133 会把参考图冲掉。故 `_EDIT_WORKFLOWS` 里每项
都显式带 `lora_node`。另注意文生图模板(`Text2IMGKrea2_api.json`)**没有 LoRA 节点**,
所以 S3 角色三视图用不了 LoRA,该路径静默忽略这个参数。

**3. 英文音色(qwentts-shim)** —— `VOICE_DESCRIPTIONS` 新增 `EN-Female` / `EN-Male`

多语种功能(英文配音 + 中英软字幕)需要。语种由 voice key 隐含,shanhai 侧
`providers/tts.py` 零改动——与当年 CosyVoice2→Qwen3-TTS 切换同一套做法。
需配合 DGX `.env` 的 `SHANHAI_TTS_VOICES` 登记与 `SHANHAI_TTS_VOICE_EN`。
实测英文约 71–88ms/字符,属正常英文语速区间。

## 同步回 DGX

```bash
scp -P 14801 scripts/dgx-shims/image-shim/main.py huntun@21.tcp.vip.cpolar.cn:~/image-shim/main.py
ssh -p 14801 huntun@21.tcp.vip.cpolar.cn "systemctl --user restart shanhai-image && curl -s http://127.0.0.1:8091/health"
```

qwentts/music/gateway 同理(服务名 `shanhai-tts` / `shanhai-music` / `shanhai-gateway`,
健康检查端口 8090 / 8092 / 8099)。
**改线上前先备份**:`cp ~/<shim>/main.py ~/<shim>/main.py.bak-$(date +%Y%m%d-%H%M)`。

日常运维排查见 [`docs/ops-dgx.md`](../../docs/ops-dgx.md)。
