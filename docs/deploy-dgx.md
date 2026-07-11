# shanhai 部署到 DGX Spark(团队内网 · 可生成 · 本地 LLM + GPU TTS)

> 状态:P0/P1 已上线(2026-07-11);P2(GPU TTS)spike 待开;P3(Ollama 适配器)已开发待部署。
> 前置:R1 冒烟见 [decisions/0006](decisions/0006-r1-local-llm-smoke.md)。

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
1. **systemd user 服务** `~/.config/systemd/user/shanhai-web.service`(WorkingDirectory=~/shanhai、ExecStart=uv run shanhai-web、Restart=always);`loginctl enable-linger`(需 sudo 则记录、先用会话内常驻兜底)。
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

## 运维速查(部署后补齐实际值)
- 服务:`systemctl --user {status,restart} shanhai-web`;日志 `journalctl --user -u shanhai-web -f`
- 隧道模板:`ssh -p 14801 -L 8080:127.0.0.1:8080 huntun@21.tcp.vip.cpolar.cn`
- 回传作品到 Mac(展示):`rsync -a huntun@…:~/shanhai/projects/ ~/Work/shanhai/projects/`
