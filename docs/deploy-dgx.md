# shanhai 部署到 DGX Spark(团队内网 · 可生成 · 本地 LLM + GPU TTS)

> 状态:P0/P1/**P2(GPU TTS)均已上线**(2026-07-11/12);P3(Ollama 适配器)已开发待部署。DGX 现为"本地 LLM+云图像+本地 GPU TTS+本地合成"完整闭环。
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

## 访问入口(2026-07-11 实际值)
- 团队内网:`http://192.168.199.107:8080`
- **公网(cpolar)**:`http://wuzitokenplan.vip.cpolar.cn/`(cpolar.yml 里 `huntun` 隧道 → :8080;注意同名 `WuziTokenPlan` 隧道指 :8000 未监听,勿混淆)。国内可达;部分国际线路到 cn_vip HTTP 边缘不通属正常。
- ⚠️ **已知风险(用户拍板接受)**:公网与内网同一实例、`readonly:false`——公网访客可触发生成烧 tu-zi 图像额度;缓解仅靠 URL 隐蔽 + 队列上限 8。若被滥用,改法见 git 历史讨论:按来源区分只读(内网可写/cpolar 只读)约半小时可加。

## 运维速查
- 服务:`systemctl --user {status,restart} shanhai-web`;日志 `journalctl --user -u shanhai-web -f`
- 隧道模板:`ssh -p 14801 -L 8080:127.0.0.1:8080 huntun@21.tcp.vip.cpolar.cn`
- 回传作品到 Mac(展示):`rsync -a huntun@…:~/shanhai/projects/ ~/Work/shanhai/projects/`
