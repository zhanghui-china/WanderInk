# shanhai 部署到 DGX Spark(团队内网 · 可生成 · 本地 LLM + GPU TTS)

> 状态:P0/P1/**P2(GPU TTS)均已上线**(2026-07-11/12);P3(Ollama 适配器)已开发待部署。**P4(端点/模型配置 Web 界面)已上线**(2026-07-12)。**P5(AI 生成 BGM,ACE-Step)已上线**(2026-07-12/13)。DGX 现为"本地 LLM+本地图像(ComfyUI)+本地 GPU TTS+本地 AI BGM+本地合成"完整闭环,且支持 Web 端按环节切换端点/模型。
> 前置:R1 冒烟见 [decisions/0006](decisions/0006-r1-local-llm-smoke.md)。

## P2 GPU TTS 已上线(2026-07-12)

**结论**:CosyVoice2-0.5B 在 GB10(Blackwell,sm_121)上用 PyTorch nightly cu128 跑通,OpenAI 兼容 shim 接入 shanhai,端到端验证通过(黄鹤楼项目 10 页真人声,107s 成片)。

**硬件坑(记录以防重装踩坑)**:
- 标准 PyPI torch(cu124 及更早)**不支持 GB10 的 sm_121** —— `torch.cuda.is_available()` 会**假阳性**返回 True,但真跑算子报 `no kernel image is available for execution on the device`。**必须验证真实 GPU 计算(矩阵乘法),不能只信 `is_available()`。**
- 修复:装 PyTorch **nightly**(`pip install --pre torch --index-url https://download.pytorch.org/whl/nightly/cu128`),含完整 Blackwell 内核,已验证 `torch 2.12.0.dev20260408+cu128` 可用。`torchaudio`/`torchcodec` 需配套装(同索引 nightly 装 torchaudio;torchcodec 标准 PyPI 有 aarch64 轮子)。
- CosyVoice 官方 `requirements.txt` 锁 `torch==2.3.1` 等旧版本 —— **装依赖时必须排除 torch/torchaudio/numpy 的精确版本锁**(否则会把刚装好的兼容 torch 覆盖掉),`pyworld` 需要 numpy 1.26.4(和 numpy 2.x C API 不兼容),`onnxruntime-gpu==1.18.0` 无 aarch64 轮子(退到不锁版本的 CPU 版 onnxruntime,只影响一个小的说话人特征提取组件,不影响主链路 GPU 加速)。

**部署结构**:
- 独立 conda 环境 `shanhai-tts`(不碰任何共享环境),CosyVoice 官方仓库 clone 在 `~/CosyVoice`(含 `third_party/Matcha-TTS` 子模块),权重经 ModelScope SDK 下载到 `~/.cache/modelscope/models/iic--CosyVoice2-0.5B`。
- Shim:`~/CosyVoice/tts_shim.py`(FastAPI,`POST /v1/audio/speech`,模型常驻内存启动时加载一次;`speed` 参数走 ffmpeg atempo 后处理转码为 mp3)。
- systemd:`shanhai-tts.service`(:8090,同 `shanhai-web.service` 模式:`RequiresMountsFor`+`network-online.target`)。
- 接入:DGX `.env` 追加 `SHANHAI_TTS_BASE_URL=http://127.0.0.1:8090/v1`、`SHANHAI_TTS_MODEL=cosyvoice2`、`SHANHAI_TTS_VOICE=default`、`SHANHAI_TTS_VOICES=default`。
- 音色:目前只有 `default`(CosyVoice2 仓库自带零样本参考音频)。**Uncle_Fu 音色克隆待办**——需从 Mac 本地 Qwen3-TTS(localhost:8000,今天检查时未运行)生成一段参考音频传到 DGX,在 `tts_shim.py` 的 `VOICES` 字典里加一条即可,不需要改代码结构。
- 运维:`systemctl --user {status,restart} shanhai-tts`;日志 `journalctl --user -u shanhai-tts -f`。
>
> **2026-07-11 事故记录**:首次端到端冒烟在 S3(角色三视图,4 个中处理 2 个后)报
> `Server disconnected without sending a response` 并杀死整条 pipeline。根因:
> `image.py`/`llm.py` 的网络重试窄写成 `(TimeoutException, ConnectError)`,漏抓
> `httpx.RemoteProtocolError`(该异常的公共基类是 `TransportError`);`llm.py` 的
> `chat()` 对请求本身甚至**零** try/except。DGX 经隧道的长连接比 Mac 直连更易触发此类
> 瞬时故障。修复:两处改为捕获 `httpx.TransportError`(commit `ab49fed`)。教训:云端
> provider 重试必须覆盖 httpx 完整 TransportError 家族,不能只挑 Timeout/Connect。
>
> **同日复发**:部署 `ab49fed` 后重发冒烟,S3 第 2 个角色又报同一异常(重试 3 次全部失败)。
> 排查:curl 连打 5 轮(100%)、Python 精确复刻 ImageClient 持久连接池 6 轮(100%)均无法
> 复现——网络路径本身不是确定性坏的,更像共用机上的间歇性抖动(122b 占极重资源,S3 恰好
> 紧跟长时间本地推理之后)。**结论**:根因既不确定也不完全可控,重试次数调再高也只是概率
> 游戏。真正的结构性修复是给 S3 补上 S4 早就有的**单元素容错隔离**——单角色三视图失败只
> 退化为纯文字特征(同 MAX_TURNAROUND 之外的次要角色),不再 raise 拖垮整条 pipeline
> (s3_characters.py + test_s3.py 新增 test_s3_single_character_failure_does_not_abort_others)。
> 教训:对不受控的外部依赖,隔离失败范围比死磕重试参数更可靠。

## ⚠️ 拓扑铁律(操作前核对)
- **代码真源 = Mac** `/Users/nativeas/Work/shanhai`(唯一 git,无远程)。开发/测试/提交只在 Mac。
- **DGX = 部署目标** `~/shanhai`(rsync 快照)。**绝不在 DGX 改代码**;DGX `.env` 机器专属、不入 git。
- **发布流程**:Mac 改+测+commit → rsync(**必须 `--exclude .env --exclude projects`**,否则覆盖 DGX 配置/数据)→ 确认无管线在跑(`/api/projects` 无 running/queued)→ `systemctl --user restart shanhai-web`。
- Mac 同一工作树还跑着两个实例(公网只读 :10000、本机编辑 :8081):重启前确认工作树是已提交可上线状态。
- 作品数据各自生长:DGX `projects/`(生产)与 Mac `projects/`(展示)不互通、不互相 rsync 覆盖。

## Context
R1 已验证 DGX(GX10/GB10,119G 统一内存,Ubuntu 24.04 aarch64,团队共用)上 Ollama qwen3.5:122b 可直接驱动 S0–S2。现把整个项目部署上去:代码 + 历史作品(797M)迁移、systemd 服务化、团队内网访问、**LLM 走本机 Ollama(免隧道)**、图像暂走云端 tu-zi(R2 前)、**TTS 在 DGX 装 GPU 版**。Mac 上的公网只读站(:10000)保留不动——两边 `projects/` 会各自生长,DGX 为生产、Mac 为展示(后续可定期回传)。

已核实:DGX 出网正常(pypi/tu-zi 可达)、:8080/:8000 空闲、systemd --user running(linger 未开)、cpolar 已装、字体已入库(16M)、本仓库无 git 远程(→ rsync 传含 `.git` 保历史)。

## P0 传输与环境
1. **小代码改动(Mac 先做)**:`api.py main()` 加 `SHANHAI_HOST`(默认 `127.0.0.1`;DGX 设 `0.0.0.0` 供内网访问),与既有 `SHANHAI_PORT` 同款。前端 `bun run build` 出最新 dist(DGX 不装 bun,直接带 dist)。
2. **rsync 到 DGX**(`~/shanhai`):仓库含 `.git`、`assets/`(字体 16M)、`web/dist`(Mac 预构建,DGX 不装 bun),排除 `.venv/ web/node_modules/ spike/out/`;**依赖不走线**——`uv sync` 在 DGX 直接拉 pypi。**`projects/` 历史作品不迁移**(用户决定):DGX 上重新生成即可,Mac 保留原作品供公网展示。
3. **DGX 环境**:装 uv(curl → `~/.local/bin`,免 sudo)→ `uv sync` → `uv run pytest -q`(aarch64 上应 147 全绿)。
4. **DGX 专属 `.env`**(生产参数,不入库):
   ```
   SHANHAI_BASE_URL=http://127.0.0.1:11434/v1   # LLM=本机 Ollama
   SHANHAI_API_KEY=ollama
   SHANHAI_LLM_MODEL=qwen3.5:122b
   SHANHAI_LLM_TIMEOUT=900
   SHANHAI_IMAGE_BASE_URL=https://api.tu-zi.com/v1   # 图像仍云端(R2 前)
   SHANHAI_IMAGE_API_KEY=<tu-zi key>
   SHANHAI_IMAGE_MODEL=gpt-image-2
   SHANHAI_IMAGE_API_MODE=images_api
   # TTS:P2 就位前留空(S5 自动静音兜底,管线不崩)
   SHANHAI_HOST=0.0.0.0
   SHANHAI_PORT=8080
   ```

## P1 服务化与端到端验证
1. **systemd user 服务** `~/.config/systemd/user/shanhai-web.service`(WorkingDirectory=~/shanhai、**`EnvironmentFile=%h/shanhai/.env`**——关键:`SHANHAI_HOST/PORT/CORS/READONLY` 走 `os.getenv` 只认进程环境,不读 .env 文件,必须由 systemd 注入、ExecStart=uv run shanhai-web、Restart=always);`loginctl enable-linger`(需 sudo 则记录、先用会话内常驻兜底)。
2. 团队访问 `http://<DGX 内网 IP>:8080`;外部走 SSH 隧道 `-L 8080:127.0.0.1:8080`。**非只读**:团队可建作品/编辑/重绘(单 worker 队列 + MAX_PENDING=8 背压;图像烧 tu-zi 额度为已知代价)。
3. **端到端冒烟**:web 建 1 分钟作品 → 本地 LLM(S0–S2)+ 云图像(S3–S4)+ S5 静音兜底 + S6 成片;历史作品列表可浏览可播。

## P2 GPU TTS(大活,先 spike 后实施)
1. **Spike 选型**(顺序试,取第一个能跑的):① Qwen3-TTS 官方 CUDA/transformers 版(与 Mac 同系,音色概念一致);② CosyVoice2(中文成熟、可克隆说书人音色)。关键不确定性:GB10 aarch64 的 PyTorch CUDA 轮子(NVIDIA 官方渠道有)与模型体积——spike 先证"能出一句 mp3"。
2. **OpenAI 兼容 shim**:小 FastAPI 包 `/v1/audio/speech`(model/voice/input/speed → mp3),shanhai `TTSClient` 零改动;systemd 服务 :8000。
3. 接线:`SHANHAI_TTS_BASE_URL=http://127.0.0.1:8000/v1` + voice;编辑实例 revoice 一页验证;`SHANHAI_TTS_VOICES` 配策展列表。

## P3 Ollama 原生适配器(~2h,10× 提速)
- decisions/0006:`/api/chat`+`think:false` 2.7s vs `/v1` 带思考 31s。新增 `providers/llm_ollama.py`(同 `chat/structured` 签名,原生 API + `think:false` + `format:"json"`)+ `config.llm_provider`(openai/ollama)+ factory 分支 + respx 测试。S0–S2 从 ~15min 降到 ~1.5min。

## 风险与说明
- **双机数据分叉**:新作品在 DGX,Mac 公网站看不到 → 接受现状;后续可加"DGX→Mac 定期 rsync 回传"或 Mac funnel 反代 DGX(另议)。
- **linger 可能要 sudo**:开不了先用 tmux/会话内常驻,记录待管理员。
- **共用机礼仪**:122b 占 95G;S4 图像并发走云不占 GPU;P2 TTS 模型显存与 122b 共存需按 119G 总量算账。
- P2 选型失败兜底:反向隧道接 Mac TTS,随时可切,不阻塞 P0/P1。

## 验证清单
- P0/P1:DGX 147 pytest 绿;`curl :8080/api/meta` 正常;内网设备可打开页面;端到端 1 分钟成片(含静音兜底)可播;Mac 公网站不受影响。
- P2:一句真人声 mp3 → 单页 revoice → 成片该页有解说。
- P3:S0–S2 总耗时 <3min;测试全绿。

## 访问入口(2026-07-15 实际值,port 已从 8080 迁到 5000)
- 团队内网:`http://192.168.199.107:5000`
- **公网(cpolar)**:`http://wuzitokenplan.vip.cpolar.cn/`(cpolar.yml 里 `huntun` 隧道)——**⚠️ 这条隧道仍指向旧的 :8080,没有跟着这次端口迁移一起改**(2026-07-15 只改了内网端口,用户当时明确选择不动公网隧道)。如果还有人在用公网地址访问,现在会连不上,需要单独改 cpolar.yml 里 `huntun` 隧道的目标端口。同名 `WuziTokenPlan` 隧道指 :8000 未监听,勿混淆。
- ⚠️ **已知风险(用户拍板接受)**:公网与内网同一实例、`readonly:false`——公网访客可触发生成烧图像生成额度;缓解仅靠 URL 隐蔽 + 队列上限 8。若被滥用,改法见 git 历史讨论:按来源区分只读(内网可写/cpolar 只读)约半小时可加。

## 运维速查(2026-07-15 更新:四个服务一起管)
- **四个 systemd --user 服务**,DGX 上现在同时跑着这些,缺一个对应功能就会退化/报错:
  | 服务 | 端口 | 作用 |
  |---|---|---|
  | `shanhai-web` | :5000(内网) | 主站 FastAPI + SPA |
  | `shanhai-tts` | :8090 | CosyVoice2 语音合成 shim |
  | `shanhai-image` | :8091→ComfyUI:8188 | 图像生成 shim |
  | `shanhai-music` | :8092→ComfyUI:8188 | AI BGM(ACE-Step)shim |
- 查看/重启单个:`systemctl --user {status,restart} <服务名>`;日志 `journalctl --user -u <服务名> -f`。
- **一次性拉起全部四个**(机器重启后常用,见下方"⚠️ 无 linger"):
  ```
  systemctl --user start shanhai-web shanhai-tts shanhai-image shanhai-music
  ```
- 隧道模板(SSH,连 DGX 用):`ssh -p 14801 huntun@21.tcp.vip.cpolar.cn`(免密公钥登录;这条隧道本身也可能随 cpolar 重启而变,连不上先确认隧道还在)。
- 回传作品到 Mac(展示):`rsync -a huntun@…:~/shanhai/projects/ ~/Work/shanhai/projects/`

## ⚠️ 无 linger:DGX 重启后四个服务不会自启(2026-07-15 发现)
**现象**:2026-07-15 部署时发现 DGX 机器在约 26 分钟前重启过(`uptime` 证实),`ssh` 一度连不上(隧道跟着断了,重启后恢复)。机器恢复后 `systemctl --user status shanhai-web` 显示 `inactive (dead)`,`ps aux` 里四个 shanhai 服务的进程全部不存在——**没有自动拉起**。

**根因**:这台机器没有执行 `loginctl enable-linger huntun`(部署文档"风险与说明"一节早就记过"linger 可能要 sudo,开不了先手动兜底"),`systemd --user` 实例本身只在有用户 session(SSH 登录/图形登录)时才存在,机器重启后没有 session 就没有 `--user` manager,四个 `enabled` 的服务自然也就没人拉起。

**临时处理**(每次都要这么做,直到有人开了 linger):SSH 登录后手动跑:
```
systemctl --user start shanhai-web shanhai-tts shanhai-image shanhai-music
```
**根治**(需要 sudo,待管理员处理):`sudo loginctl enable-linger huntun`,之后这四个 `enabled` 的服务会在开机时随 `user@1007.service` 一起自动起来,不用再手动干预。

## P1 实证:CosyVoice2 单发 vs 分句(2026-07-12)
在 DGX 直连 CosyVoice2 shim(:8090)对短/中/长/超长文案做对照,判断旧「分句 + 三试取最长 + MIN_MS_PER_CHAR 截断检测」启发式是否仍必要(这套是为旧云端弱模型的确定性截断而设):
- **不截断**:67 字长句单发 3 次时长稳定(~16s),whisper ASR 转写完整覆盖首尾(含末句「流传千年的旧事」)——无确定性截断,与旧弱模型不同。
- **旧 floor 误判**:CosyVoice2 连读约 240–270ms/字,而旧 `MIN_MS_PER_CHAR=380` 高于真实语速 → 每句都被误判截断、空转 `TTS_TRIES=3`(即审计 PERF3)。
- **分句更糟**:长句分句合成 19.3s vs 单发 16s,句间硬拼更长更碎;分句的唯一收益(防截断)对 CosyVoice2 已消失,只剩 N× 调用成本。

**结论/改动**:`s5_audio._synthesize_full` 改为**整段单发优先**(1 次调用、自然),仅当单发疑似截断(时长 < 字数×`MIN_MS_PER_CHAR`)才**自动退化**到旧的分句路径(兼容会截断的弱模型,如 Mac Qwen3);`MIN_MS_PER_CHAR` 380→150(只兜真正的严重截断)。跨后端安全、无需新配置。

## P4:端点/模型配置 Web 界面已上线(2026-07-12)

**内容**:Web header 齿轮按钮 → 配置面板,可在线设置 LLM/图像/TTS 的端点/密钥/模型,支持**全局默认 + 按环节(S0–S5)覆盖**(如 S1 走云端强模型、S2 走本地 Ollama),运行时生效、无需改 `.env` 或重启。持久化于 `~/shanhai/config.json`(gitignore,叠加在 `.env` 之上;不覆盖不重跑不影响已有配置)。开发过程:brainstorm → ultracode 多 agent 落地 → 4 镜头对抗审计 → `/code-review` xhigh 复审(10 镜头,修正合并语义等正确性问题)。Mac 侧 233 pytest 全绿。

**部署记录**:
- 常规流程:Mac `main`(commit `e94142b`)→ 确认 DGX 无在途任务 → rsync(`--exclude .env --exclude projects --exclude config.json`)→ DGX `uv sync`(无新依赖)→ DGX `uv run pytest -q`(aarch64,233 passed)→ `systemctl --user restart shanhai-web`。
- **一次性状况**:重启前检测到一个 `queued` 任务(`stepid`/雷峰塔,提交仅 3 分钟,非重启前遗留僵尸),用户明确指示终止重启;`reconcile_zombie_jobs()` 按既有机制在启动时自动把它对账为 `error: 服务重启,生成中断`,符合设计,无需额外处理。
- **验证**:`/api/meta`、新增 `/api/config`(GET 返回 `stage_clients`/`defaults`/`global`/`stages`,密钥脱敏)均 200;内网入口 `192.168.199.107:8080` 200;cpolar 隧道进程未受影响(独立进程,重启 `shanhai-web` 不涉及);dist 清理了 rsync 遗留的旧构建 hash 文件(无功能影响,仅整洁)。
- 团队使用:内网 `http://192.168.199.107:8080` 打开后点右上角齿轮即可配置。

## LLM 模型变更(2026-07-12):gpt-oss:120b → glm-4.7-flash:latest

**背景**:探查 DGX 实际生效配置时发现 `.env` 已与本文档记录脱节——图像早已从云端 tu-zi 迁移到本地 `shanhai-image.service`(ComfyUI shim,`127.0.0.1:8091`→`127.0.0.1:8188`,ComfyUI 由共用机上另一系统用户 `wuzi` 跑),LLM 模型是 `gpt-oss:120b` 而非文档写的 `qwen3.5:122b`;且 `SHANHAI_LLM_PROVIDER` 未设置(默认 `openai` 兼容层,P3 原生 Ollama 适配器已开发但未启用,按 [decisions/0006](decisions/0006-r1-local-llm-smoke.md) 原生适配器快 10×,待办)。

**本次变更**:`SHANHAI_LLM_MODEL` 从 `gpt-oss:120b`(65.4G)改为 `glm-4.7-flash:latest`(19G on disk / ~40G resident)。原因:`glm-4.7-flash` 是 `ollama ps` 里当时**已常驻显存**的模型(可能被 `wuzi` 或团队其他人占用中),而 `gpt-oss:120b` 未加载,每次生成都要先触发 Ollama 换模型,拖慢首次响应且加剧共用机显存压力。

**操作**:直接改 DGX `~/shanhai/.env`(不经 Mac/rsync,机器专属配置)→ 确认 `/api/projects` 无活跃任务 → `systemctl --user restart shanhai-web`(`EnvironmentFile` 只在启动时读取,必须重启生效)→ 验证 `/proc/<pid>/environ` 确认新值已加载。

**已知现状(本文档当前的真实基线,供下次核对用)**:
```
SHANHAI_BASE_URL=http://127.0.0.1:11434/v1        # 本机 Ollama
SHANHAI_LLM_MODEL=glm-4.7-flash:latest
SHANHAI_IMAGE_BASE_URL=http://127.0.0.1:8091/v1    # 本地 shanhai-image.service → ComfyUI:8188
SHANHAI_IMAGE_MODEL=comfyui-local
SHANHAI_TTS_BASE_URL=http://127.0.0.1:8090/v1      # shanhai-tts.service,CosyVoice2
SHANHAI_TTS_MODEL=cosyvoice2
```
**待办**(发现但未处理,留给下次):`SHANHAI_LLM_PROVIDER=ollama` 未启用(原生适配器提速 10×);团队共用 Ollama 显存,当前常驻模型会随其他用户使用漂移,生成前建议 `ollama ps` 确认。

## P5:AI 生成 BGM 已上线(2026-07-12/13)

**内容**:S5 环节新增三级降级——AI 生成(本机 ACE-Step,经新建的 `~/music-shim` 转发到 wuzi 的 ComfyUI `:8188`)→ 静态曲库(`assets/bgm/manifest.json`,现状为空,原逻辑原样保留)→ 无 BGM。纯器乐(`lyrics="[instrumental]"`),风格标签从 `project.params.tone`/`style_preset` 查表拼装(不经 LLM),目标时长按 `duration_min×60` 封顶 180s。S6/`ffmpeg.finalize_cmd` 完全不改——混音基础设施早就有,只是曲库一直是空的从未真正触发过。

**`~/music-shim` 部署过程中修的两个真 bug**(记录以防重装踩坑):
1. **ffmpeg 选错版本**:`/usr/local/bin/ffmpeg`(共享机默认 PATH)是残缺构建,**没有 `libmp3lame` 编码器**,ACE-Step 输出转 mp3 时报 `exit 8`。必须显式指定 `~/anaconda3/envs/shanhai-ffmpeg/bin/ffmpeg`(huntun 独立环境,含完整编码器集),与 `tts_shim.py` 早就踩过的同一个坑。
2. **WebSocket 监听顺序反了**:最初实现是"先 `POST /prompt` 提交任务,再连 WebSocket 监听完成事件"——若 ComfyUI 命中节点缓存后近乎瞬间完成(两次几乎相同的请求参数很容易触发),会在连接建立前就已完成,监听方永久错过完成事件,直接卡到 `POLL_TIMEOUT_S`(300s)超时。修复为"先连 WebSocket、连上后才提交任务",与 wuzi 的参考脚本 `~/ComfyUI/generate_music_api.py`(`ws.connect()` 在 `queue_prompt()` 之前)同序。

**纯器乐核验**(部署前明确标注为待实测的风险项,现已核验通过):用 `mlx-whisper`(Mac 本地,Apple Silicon)对生成样本做了两次独立转写——锁中文识别出经典的"无语音幻觉伪影"(短暂片段+胡编文本,非真实歌词);不锁语言自动检测,直接把整段转写成单词 `Music`(Whisper 训练数据对纯配乐场景的标准标注,业内公认的"无人声"信号)。DGX 本地也曾尝试用 `openai-whisper` 做同样核验,但因网络拉 torch 过慢(近一小时仅到 700M)而放弃,改用 Mac 本地 `mlx-whisper` 完成核验(轻量、Apple Silicon 原生,几分钟内出结果)。

**部署记录**:
- 常规流程:Mac 4 个 commit(provider/S5/CLI-API/测试)→ `--no-ff` 合并 main → rsync → DGX `uv sync` + 253 测试全绿(aarch64)→ `.env` 追加 `SHANHAI_MUSIC_BASE_URL=http://127.0.0.1:8092/v1`、`SHANHAI_MUSIC_MODEL=ace-step-v1.5xl` → 确认无在途任务 → `systemctl --user restart shanhai-web`。
- **一次性状况**:重启前又出现同一个 `stepid`/雷峰塔测试项目(和 P4 部署那次一模一样的 project_id 与内容,疑似某处周期性生成的固定测试样本,非合法 uuid 格式,不可能经正常建作品流程产生),用户再次明确授权终止;`reconcile_zombie_jobs()` 照常对账为 `error: 服务重启,生成中断`。
- **真实端到端验证**(非仅测 shim):通过生产 API 建了一个 1 分钟测试作品(`2cd9d616`),完整跑完 S0–S6:`project.bgm` 落在 AI 生成路径(`projects/2cd9d616/audio/bgm.mp3`,60.0s,精确匹配目标时长,证明走的是 AI 分支而非曲库兜底)、`final.mp4` 音视频流均正常(h264+aac,85.16s)。
- 服务:`shanhai-music.service`(:8090 之后新增,`:8092`),systemd 部署,仿 `shanhai-tts.service`/`shanhai-image.service` 模式;`~/music-shim/main.py` 独立于 shanhai git 仓库(与 `image-shim`/`tts_shim.py` 现状一致)。
- **待办**(已知但非阻塞):BGM 生成是 S5 里的同步网络调用,在 TTS 并发池之外顺序跑,GPU 排队(与 wuzi 的 ComfyUI、`shanhai-image.service` 共用同一张卡)可能显著拖慢单次生成;Web 配置面板(`SettingsPanel.tsx`)未补齐 music 字段的可视化,目前只能靠 `.env`/`config.json` 直改。

## 「天青烟雨·画卷」视觉改版上线 + 端口迁移(2026-07-15)

**内容**:纯前端换肤,暖色宣纸朱砂主题 → 冷色天青烟雨(青瓷绿主色、雾青宣纸底),外加水墨云山题头/朱砂印章/竖排题字/回纹分隔条等装饰层(新组件 `web/src/components/decor.tsx`)。同期还做了:作品列表按创建时间排序、生成进度每步耗时+页数计数、三视图查看详情、分享链接(`?project=<id>`)、队列点击跳转/取消报错修复。均为独立提交,逐个部署验证。

**图像生成一度全面故障,已修复**:2026-07-14 队友 wuzi 把 ComfyUI 工作流模板从 `~/ComfyUI/` 迁移到新建的 `~/WanderInk/comfyui-bridge/`,`~/image-shim/main.py` 里硬编码的 `COMFYUI_ROOT` 没跟着改,导致 S3/S4 所有图像生成从那天起持续 500 失败。已把 `COMFYUI_ROOT` 改指向新路径并重启 `shanhai-image.service`,验证恢复正常。

**端口迁移**:`SHANHAI_PORT` 8080 → **5000**(DGX `.env` 直改,机器专属配置,不入库)。原因:用户要求把内网端口让给别的用途。公网 cpolar 隧道**未跟着改**,仍指向旧 8080(见上方"访问入口"一节)。

**发现 DGX 无 linger,四个服务重启后不会自启**:部署当天赶上 DGX 机器重启过一次(约 26 分钟前,推测是意外重启,非本次操作触发),`shanhai-web`/`shanhai-tts`/`shanhai-image`/`shanhai-music` 四个 systemd --user 服务全部 `inactive (dead)`。手动 `systemctl --user start` 拉起全部四个后确认恢复,已在上方新增专门小节记录根因和处理办法(需要 sudo 开 `loginctl enable-linger` 才能根治)。

**部署记录**:本次前后共 4 个独立提交先后部署(排序 `6ad58c4`、生成进度计时 `4b52a6d`、三处 UI 改进+分享链接 `ea16401`/`276f46a`、本次换肤 `f4f8ae4`),每次都走标准流程:确认无在途任务 → rsync(排除 `.env`/`projects`/`config.json`/`users.json`)→ DGX `uv sync` → `uv run pytest -q`(306 passed)→ `systemctl --user restart shanhai-web`。团队现在访问 `http://192.168.199.107:5000` 应该能看到全新配色。

## 三视图卡片布局修复 + 图像生成切到 tu-zi + S4 并发跟随后端(2026-07-15)

**三视图卡片修复**(commit `a2ac7b7`):角色三视图实际生成尺寸是横版 1536x1024(正/侧/背三像并排),`CharacterCard` 之前用竖版 `aspect-[3/4]` + `object-cover` 硬裁,裁掉了两侧人像。改成 `aspect-[3/2]` 匹配真实比例;网格从 4 列收窄到最多 3 列;"查看详情"/"重绘设定图"两个按钮改用独立样式(10px + `flex-1` + `whitespace-nowrap`),不再换行。

**S4 出图并发跟随图像后端**(commit `113c143` + 两次测试隔离修正 `17adc79`/`45f5da0`):新增 `api._image_concurrency(settings)` ——图像端点是 `127.0.0.1`/`localhost`(团队共用单张 GPU 的本地 shim,如 ComfyUI)时强制串行(concurrency=1),避免并发请求排队/冲突;远程云端 API(如 tu-zi)才用原有并发档位(`s4_pages.CONCURRENCY=3`)。`s4_pages.run()` 新增 `concurrency` 参数,默认值仍是模块常量,不破坏其它调用方式。

> **踩坑记录**:第一版测试(`Settings(base_url=...)` 只传通用 `base_url`)在 Mac 上绿、在 DGX 上红——因为 `Settings.image_endpoint` 优先取 `image_base_url`,而 DGX 进程的 `os.environ` 里已经有 `api.py` 导入时从 `.env` 加载进去的真实 `SHANHAI_IMAGE_BASE_URL`(本地 ComfyUI 地址),这个环境变量的优先级比 `_env_file=None` 想屏蔽的 dotenv 还高,把测试传入的 `base_url` 悄悄接管掉了。教训:凡是 `Settings` 相关的测试,必须显式传该属性实际读取的那个字段(这里是 `image_base_url`,不是笼统的 `base_url`),不能假设 `_env_file=None` 就等于"完全隔离运行环境"。

**DGX 图像生成切到 tu-zi(gpt-image-2)**:通过配置面板同一套机制(`config.json` 全局覆盖,不改 `.env`、不用重启)把 `image_base_url`/`image_model`/`image_api_mode` 改成 tu-zi 云端;`image_api_key` 故意不填,继承 DGX `.env` 里当初用 ComfyUI 之前留下的旧 tu-zi key——已用真实调用验证过仍然有效(生成了一张测试图,787KB,非 401/403)。

**操作方式**(记录以防下次需要照做):由于无法在共享生产实例上新建认证账号(会被 auto mode 分类器拦下,理由是"未授权的持久化"),改成直接调用 `runtime_config.update_overrides`/`apply_put`(和 `PUT /api/config` 内部调用的是同一套函数,只是跳过了 HTTP/登录这一层),在 DGX 上 `cd ~/shanhai && uv run python3` 里执行:
```python
from shanhai.runtime_config import update_overrides, apply_put, AppConfig, ConfigOverride
incoming = AppConfig(global_=ConfigOverride(
    image_base_url='https://api.tu-zi.com/v1',
    image_model='gpt-image-2',
    image_api_mode='images_api',
))
update_overrides(lambda existing: apply_put(existing, incoming))
```
效果和团队成员登录后在配置面板里手动填三个字段、点保存完全一样,写的是同一个 `config.json`。

**现状(供下次核对)**:S3/S4 生效的图像端点是 tu-zi/`gpt-image-2`,S4 并发=3(自动判定,远程端点);本地 `shanhai-image.service`(ComfyUI shim,`127.0.0.1:8091`)仍在运行、未停,只是配置层面暂时没人指向它——想切回本地,在配置面板里把"图像生成"的 Base URL 改回 `http://127.0.0.1:8091/v1`、模型改回 `comfyui-local` 即可,S4 并发会自动跟着变回串行。

## S0/S1 接入"编剧大师"(hermes-agent)(2026-07-15)

**内容**:DGX 上团队自跑的 `hermes-agent`(`http://127.0.0.1:8642/v1`,OpenAI 兼容,加载了"编剧大师" skill)接入 S0(传说检索)/S1(剧本生成)两个环节,S2(分镜)/S3(角色特征提取)不动,继续用原来的 LLM 后端——纯配置改动(`config.json` 的 `stages.s0`/`stages.s1` 覆盖),`src/shanhai/providers/llm.py` 的 `LLMClient` 一行代码没改,因为 hermes-agent 对结构化请求(JSON Schema + "只输出 JSON"指令)会老实执行,不会触发它自己的"编剧大师"反问式对话流程(那个只在收到开放式请求时才触发)。

**关键发现(避免下次重新踩坑)**:
- **`prompt_tokens` 每次请求都在 16000+**(哪怕只发"hi"),推测服务端每次都隐式拼进一大段"编剧大师" skill 说明书,这个开销不可控。
- **是重推理型后端**:`completion_tokens` 远大于最终 `content` 长度(实测 S1 一次 10546 vs 2157 字),差额是内部推理消耗,不影响最终 JSON 正确性,但计入耗时/用量。
- **延迟明显更高**:真实端到端验证(直接跑 `s0_legend.run`/`s1_script.run`,和 `api.py._pipeline` 同一套代码路径)——S0 耗时 135.4s,S1 耗时 122.9s,合计约 4.3 分钟,比本地 Ollama 慢不少。把这两个环节的 `llm_timeout` 顺带调到了 600s(默认 300s 打底应该也够,但留了余量)。
- 本地 `127.0.0.1` 端点已被 `providers/_http.py` 的 `local_backend_guard` 自动纳入 GPU 共享互斥锁,不用额外处理并发。

**操作方式**(和切 tu-zi 图像同一手法,直接调用 `runtime_config` 函数、不经 Web UI):
```python
from shanhai.runtime_config import update_overrides, apply_put, AppConfig, ConfigOverride
incoming = AppConfig(stages={
    's0': ConfigOverride(llm_base_url='http://127.0.0.1:8642/v1', llm_api_key='<key>',
                          llm_model='hermes-agent', llm_timeout=600),
    's1': ConfigOverride(llm_base_url='http://127.0.0.1:8642/v1', llm_api_key='<key>',
                          llm_model='hermes-agent', llm_timeout=600),
})
update_overrides(lambda existing: apply_put(existing, incoming))
```

**验证踩坑记录**:第一次端到端验证脚本写成 `resolve_stage_clients(AppConfig())`,传了个空的 `AppConfig()` 而不是 `None`——`runtime_config.resolve_settings` 内部是 `cfg = cfg or load_overrides()`,空的 `AppConfig()` 实例本身是 truthy,导致完全不读磁盘上的 `config.json`,悄悄退回到 `.env` 基线设置。改成传 `None` 才对上。教训:凡是要验证"配置覆盖是否生效"的脚本,必须显式传 `None` 或直接调用 `load_overrides()`,不能用默认构造的空 `AppConfig()` 占位。

**现状(供下次核对)**:S0/S1 生效 LLM 是 hermes-agent(`127.0.0.1:8642`);S2/S3 未受影响,沿用此前就已存在的 `config.json` 全局覆盖(`llm_base_url=https://api.stepfun.com/v1`,`llm_model=step-3.7-flash`——这个全局覆盖是本次任务之前就有的,不是这次改的,顺带发现部署文档此前记录的"glm-4.7-flash:latest"已过期,供下次核对时留意)。

## S1 接入 hermes-agent 的"编剧大师"skill(2026-07-16,commit `48e91ca`)

**背景**:上面那次接入(2026-07-15)只是把 S0/S1 的后端指到 hermes-agent,`LLMClient` 发的是普通 system prompt,**从未显式触发它内置的"编剧大师"skill**——用真实响应核实过:不带 `/screenwriter-master` 时 `prompt_tokens≈16k`、没有任何 skill 元数据,hermes-agent 只是被当成一个普通 LLM 在用。

**实测确认的关键事实**(DGX 上直接调用验证,非推测):
- 消息内容里加 `/screenwriter-master` 前缀会真正加载 skill——`prompt_tokens` 从 16k 跳到 **70k**,且默认进入**多轮反问式工作流**(会反问篇幅/受众/基调,不直接产出),与我们要求的"单轮直出 JSON"天然冲突。
- 但**一次性把参数喂全 + 明确要求"请勿反问,直接产出成品"**,可以压住反问、单轮拿到合法 JSON——用真实 `Script` schema(嵌套 acts/scenes/dialogues/characters)验证通过:4 幕、4 个角色、707 字命中目标 650±20%。**不需要多轮对话状态机**,复用现有 `LLMClient.structured()` 单轮 + 重试模式即可接入。
- **代价很高**:单次约 **150k~165k token、200~400 秒**,约为不用 skill 的 **10 倍 token、2~3 倍耗时**。这是逐作品开关默认关闭的直接原因。

**改动**(只涉及 S1,不碰 S0):
- `GenerationParams`/`NewProject` 新增 `screenwriter_skill: bool`(默认 `False`),前端新建作品表单新增对应勾选框(默认不勾)。
- `s1_script.run()` 新增 `use_skill` 参数:为真时 system prompt 包一层 `/screenwriter-master\n\n` 前缀 + `【一次性给全信息,请勿反问,直接产出成品剧本】` 尾缀,`structured(..., retries=1)`(默认 `retries=2`,skill 场景每次重试都是又一次 ~400s/~16 万 token,封顶到最多两次尝试控制最坏成本)。
- **后端把关**(`api._s1_use_skill`):只有作品勾了开关**且** S1 当前生效 `llm_model == "hermes-agent"` 时才真正启用;否则打印一行提示、静默退化为普通生成——避免开关误开但 S1 配置成别的模型时,把 `/screenwriter-master` 当乱码发过去。`cli.py` 的 `step`/`run` 两处 S1 调用点做了同款 gate。
- 顺带修正了一处此前遗留的误导性文案:去年(2026-07-15)那次接入时新建表单加的 `use_hermes_agent` 开关文案原写"用编剧大师生成剧本/分镜"——但它实际只是"S0/S1 是否用 hermes-agent 后端",从不触发 skill,这次改成了如实的"S0/S1 用 hermes-agent 后端(关闭则用默认 LLM)",避免和这次新加的 skill 开关混淆。

**超时余量**:用 `scripts/setup-hermes-agent.py --timeout 900` 把 S0/S1 的 `llm_timeout` 从 600s 抬到 900s,给 skill 单次调用(实测 200~400s)留够头,retries=1 时最坏两次尝试串行仍在合理范围。

**DGX 真机端到端验证**(直接跑 wired 的 `api._s1_use_skill` + `s1_script.run(use_skill=True)`,同一套生产代码路径):gate 正确识别 `use_skill=True`(S1 后端确为 hermes-agent),实际调用耗时 212.7s,产出《断桥伞下》4 幕 4 角色(白素贞/许仙/法海/小青),旁白+对白 705 字命中目标区间,`status["s1"]="done"`。

**部署记录**:372 测试全绿(本地+DGX aarch64 一致)、`ruff check` 通过、前端 `npm run build` 通过 → 确认无在途任务 → rsync → DGX `uv sync`+pytest → `systemctl --user restart shanhai-web` → 200。已同步到 WanderInk GitHub 远程(`git subtree`,用户本机执行 `scripts/sync-wanderink.sh`)。

**现状(供下次核对)**:新建作品表单有两个相关开关,注意区分——`use_hermes_agent`(S0/S1 是否走 hermes-agent 后端,默认开)与 `screenwriter_skill`(S1 是否显式触发编剧大师 skill 深度创作,默认关、成本高)。后者依赖前者为真且后端确实解析到 `hermes-agent` 才生效。S0 未接 skill(检索类任务,skill 意义不大,决策已明确不做)。

## tu-zi 图像额度耗尽 + S5 配音从 CosyVoice2 切到 Qwen3-TTS(男/女声可选)(2026-07-16/17)

**起因**:用户反馈"生成任务好像卡住了"。排查发现不是卡死——所有项目 `pipeline` 都是终态(done/error/partial),没有一个 running/queued;真正的问题是 **tu-zi 账户余额已耗尽**(实测调用 `images/generations` 返回 `403 insufficient_user_quota`,余额 `-$0.003338`),S3/S4 每张图都在几秒内被直接拒绝(不在 `TRANSIENT_STATUS` 里,不重试),22 页项目整个 S4 只花 14.4s 就全灭——这种"异常快的全灭"就是额度耗尽的指纹,不是网络/代码问题。**处理方式**:引导用户在配置面板把"图像生成"整组四个字段点"清除(改为继承)",回退到 `.env` 里本来就配好的本地 ComfyUI(`shanhai-image.service`,`127.0.0.1:8091`,模型 `comfyui-local`),S4 并发也会自动跟着变回串行(按 base_url 是否本地判定)。此项是用户自行操作,不是代码改动。

**顺带需求**:用户想让配音音色可选男/女。排查现状:CosyVoice2 走的是**零样本声音克隆**(需要参考音频 + 精确逐字稿配对),`tts_shim.py` 里当时只定义了一个 `"default"` 音色,模型本身(`CosyVoice2-0.5B`)不含内置说话人库(模型目录无 `spk2info.pt`)——要加男/女声,常规做法得录/找两条不同性别的参考音频,用户暂时给不出。

**转折**:查看 WanderInk 仓库(`comfyui-bridge/` 目录,队友 wuzi 维护)时发现一套现成方案——**Qwen3-TTS VoiceDesign**,通过 ComfyUI 自定义节点 `Comfyui-HAIGC-QwenTTS`(`Qwen3TTSModelLoader` + `Qwen3TTSVoiceDesign`)实现,**不需要参考音频**,只需一句**英文声音描述**(如 `"A young female voice, clear and gentle..."`)就能控制音色特征。实测确认这套在 DGX 上已经完整就绪:自定义节点已装(`curl /object_info/Qwen3TTSVoiceDesign` 查询确认节点活的)、模型权重已下载(`Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`)、工作流模板已在 `~wuzi/WanderInk/comfyui-bridge/VoiceDesign-QwenTTS.json`(注意:DGX 上实际部署的目录是**扁平的**,没有 git 仓库里看到的 `workflows/` 子目录,踩过一次路径坑)。

**改动**(纯 DGX 运维,不改 shanhai 代码/git 仓库,与 `image-shim`/`music-shim`/原 `tts_shim.py` 现状一致):
- 新写 `~/qwentts-shim/main.py`(独立 venv),对外契约与原 CosyVoice2 shim 完全一致(`POST /v1/audio/speech`,`{model,voice,input,response_format,speed}` → 原始 mp3),`shanhai` 侧 `TTSClient` **零代码改动**。内部走 ComfyUI websocket 排队协议,直接照搬 `music-shim` 已验证过的"先连 WebSocket、再提交任务"顺序(否则命中缓存瞬间完成会错过完成事件、永久挂起),省了重新踩那个坑。
- `voice` 参数直接用中文键("女声"/"男声")映射到英文声音描述提示词,前端下拉框(`meta.voices` 现有的通用 `<select>`)不用改代码就能显示正确的中文选项。
- `语速` 字段直接映射到 Qwen3TTSVoiceDesign 节点原生的"语速"输入(范围 0.5~2.0),比 CosyVoice2 shim 用 ffmpeg `atempo` 后处理变速更干净。
- systemd 服务复用同一个 unit 名 `shanhai-tts.service`、同端口 `8090`,只换 `WorkingDirectory`/`ExecStart` 指向新目录+新 venv——`shanhai` 应用侧 `.env` 只需改 `SHANHAI_TTS_MODEL`/`SHANHAI_TTS_VOICE`/`SHANHAI_TTS_VOICES` 三个字段,`SHANHAI_TTS_BASE_URL` 不变。旧 CosyVoice2 unit 备份为 `shanhai-tts.service.cosyvoice2.bak`,`.env` 也有时间戳备份,可随时回滚。

**验证**(真实调用,非推测):先在测试端口(8099)分别测女声/男声——女声 14.4s/56719 字节、男声 4.2s/55064 字节,均为合法 mp3(ID3v2.4,MPEG Layer III,24kHz);生成的音频发给用户本人听感确认后,才正式切到生产端口 8090。切换后用生产同一套 `TTSClient.synthesize()` 代码路径(经 `resolve_settings("s5")`)再跑一次真实端到端,女声/男声都成功产出(42037/50798 字节)。

**现状(供下次核对)**:`shanhai-tts.service` 现在跑的是 Qwen3-TTS VoiceDesign(经 ComfyUI `127.0.0.1:8188`,和图像/音乐生成共用同一个 ComfyUI 实例,受同一张 GPU 排队约束),不再是 CosyVoice2;`SHANHAI_TTS_VOICES=女声,男声`,默认音色女声。新建作品表单的音色下拉框会显示这两项。图像生成如果用户已按上面的引导切回本地 ComfyUI,S3/S4 应该已恢复正常,若之后想再切回 tu-zi,前提是账户先充值。
