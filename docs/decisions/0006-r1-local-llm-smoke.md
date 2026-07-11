# 0006:R1 本地 LLM 冒烟——DGX Spark + Ollama qwen3.5:122b 跑通 S0–S2

- 日期:2026-07-11
- 结论:**GO**。故事质量达标,零业务代码改动(仅超时可配),延迟偏高有明确 10× 提速路径。

## 环境
- 机器:GX10(DGX Spark 同级),NVIDIA GB10,119GB 统一内存,aarch64,Ubuntu 24.04;团队共用。
- 接入:cpolar SSH 隧道(`ssh -L 11435:127.0.0.1:11434 -p 14801 huntun@21.tcp.vip.cpolar.cn`,密钥免密)。
- 服务:Ollama 0.24(:11434,OpenAI 兼容 `/v1`),模型库存:qwen3.5:122b / qwen3.5:35b-a3b / gpt-oss:120b / glm-4.7-flash 等。

## 跑法(纯配置切换,验证了 R1"配置级迁移"假设)
```bash
SHANHAI_BASE_URL=http://127.0.0.1:11435/v1 SHANHAI_API_KEY=ollama \
SHANHAI_LLM_MODEL=qwen3.5:122b SHANHAI_LLM_TIMEOUT=900 \
uv run shanhai new 雷峰塔 --minutes 1   # 再 pick / step s1 / step s2
```

## 结果(项目 17e01dd0,对照 gpt-5.5 项目 9e683f5b)
| 项 | qwen3.5:122b @ DGX | gpt-5.5 云端 |
|---|---|---|
| S0 候选质量 | 3 个,来源类型齐全,含真实史实"塔砖辟邪致塔倒"(1924 实事) | 相当 |
| S1 剧本 | 结构完整,角色按重要度排序(S1 排序约束被遵守),旁白排除生效 | 相当 |
| S2 分镜 | **10 页命中 1min 目标**;visual_desc 带镜头语言(全景/中景/特写、光线);旁白 20–37 字全≤80 | 22 页命中 3min 目标;意象略更丰富 |
| 耗时 | S0 210s / S1 389s / S2 286s(思考型模型) | 每步 ~10–30s |

## 发现与修复
1. **思考型模型超时**:qwen3.5:122b 带 reasoning,S1 单次 389s > 原硬编码 300s → 必超时。已修:`SHANHAI_LLM_TIMEOUT`(config/llm/cli,默认 300 不变)。
2. **10× 提速路径(R1 后续)**:Ollama 原生 `/api/chat` + `think:false` 实测 **2.7s vs /v1 带思考 31s**(同 122b 同问题);`/no_think` 提示词与 `/v1` 参数透传均无效。→ 需一个 ~40 行的 Ollama 原生适配器(同 `chat/structured` 签名,factory 选择),预计 S0–S2 总耗时从 ~15min 降到 ~1.5min。
3. 首次加载 81GB 模型 ~49s,keep-alive 后不再付;262k 上下文配置充裕。

## R2–R4 前置盘点(顺带)
- R2 图像:队友已装 ComfyUI(`/home1/zhanghui/ComfyUI`,未运行),checkpoints 仅 SD1.5——**缺 Qwen-Image,需下载模型**。
- R3 音乐:ACE-STEP 未装。
- TTS:DGX 上无;当前用 Mac 本地 Qwen3-TTS。
- 共用机礼仪:跑前 `free -h` + `ollama ps` 确认无人占用;122b 占 95GB。
