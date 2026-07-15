# 景区有声连环画 Agent — 产品方案（优化版）

> **文档版本**：v2.1（模型栈与工程实现对齐）

> **更新日期**：2026-07-15

> **优化说明**：基于原文档进行结构重组、内容补全、产品视角增强；v2.1 按仓库/DGX 实际部署的模型与接入方式修订

---

## 目录

1. 项目速览

2. 产品定义与用户价值

3. 架构总览

4. 服务器环境

5. 环境搭建

6. Phase 1：故事生成与剧本创作

7. Phase 2：角色设计与素材生成

8. Phase 3：漫画生成与后期

9. Phase 4：配音配乐与最终合成

10. 完整流水线编排

11. Demo 展示剧本

12. 功能模块清单

13. 团队分工与里程碑

14. 讨论与开放问题

15. 产品评审检查清单（新增）

---

## 0\. 项目速览

### 一句话 Pitch

给定一个景区名称，系统自动生成历史故事、剧本、分镜、角色设计、漫画画面、配音配乐，最终合成一部有声连环画——全程在 NVIDIA DGX Spark 单机上运行，无需人工干预。

### 项目背景与动机

**为什么做景区有声连环画？**

- **行业痛点**：景区文化 IP 开发严重依赖人工创作团队（编剧、画师、配音、配乐），制作周期长、成本高，中小景区难以负担

- **现有 AI 方案不足**：通用 LLM 只能生成文本，图片生成模型缺乏角色一致性，TTS/音乐生成与画面脱节，没有端到端的全链路自动化方案

- **机会**：DGX Spark 119GB 统一内存使得多模型协同推理成为可能，将故事生成、图像生成、语音合成、音乐生成压缩到单机流水线中，实现"景区名称进、有声连环画出"的全自动化

### 核心创新点

16. **端到端全链路自动化**：从景区名称到最终有声连环画，12 个功能模块全自动串联，无需人工干预

17. **角色一致性保障**：通过 Qwen\-Image\-Edit\-2511 \+ LoRA 微调，在分镜漫画生成阶段保持人物外观一致性（三视图 → 分镜复用）

18. **多模型分时编排**：119GB 单机约束下，LLM、图像生成模型、TTS 模型、音乐生成模型通过 Supervisor 分时加载，峰值不超内存上限

19. **NVIDIA 全栈落地**：DGX Spark 统一内存 + Ollama（glm-4.7-flash）+ ComfyUI（Qwen-Image-Edit-2511 / Krea2）+ CosyVoice2 TTS + ACE-STEP 1.5 XL 音乐，展示完整创意 AI 生命周期

20. **Skill 模块化设计**：每个功能模块抽象为独立 Skill（编剧 skill、导演 skill、画师 skill 等），支持灵活扩展新场景

### 竞赛评审对标

评审维度 \| 对标点

NVIDIA 技术栈覆盖 \| DGX Spark 统一内存、Ollama / ComfyUI GPU 加速、本地多模态闭环

创新性 \| 端到端有声连环画全自动生成，业界首个单机全链路方案

完成度 \| 12 个模块全部跑通，产出可播放的有声连环画

Demo 效果 \| 输入景区名 → 3 分钟内输出有声连环画，现场可见

---

## 1\. 产品定义与用户价值（新增）

### 1\.1 目标用户

用户群体 \| 使用场景 \| 核心诉求

景区运营方 \| 景区文化宣传、游客导览 \| 低成本快速生成 IP 内容

文旅创作者 \| 素材灵感、二次创作 \| 降低创作门槛，快速出稿

竞赛评委 / 技术社区 \| NVIDIA 技术栈展示 \| 看到完整 AI 创意流水线

### 1\.2 核心 User Story

- **US\-1**：作为景区运营方，我希望输入景区名称即可获得有声连环画，这样我无需组建专业团队就能制作文化宣传内容

- **US\-2**：作为观众，我希望在 3 分钟内看到从输入到成品的全流程演示，这样我能直观感受 AI 全链路自动化的效果

- **US\-3**：作为开发者，我希望每个功能模块独立可测试，这样我能快速定位和修复问题

### 1\.3 成功指标（SLA）

指标 \| 目标值 \| 说明

端到端生成时间 \| ≤ 5 分钟 \| 从输入景区名到产出最终视频

故事文本质量 \| 人工评分 ≥ 4/5 \| 起承转合完整，人物鲜明

角色一致性 \| 相似度 ≥ 85% \| 同角色在不同分镜中的外观一致性

语音合成自然度 \| MOS ≥ 4\.0 \| 解说语音的听觉自然度

流水线成功率 \| ≥ 90% \| 无人工干预的端到端跑通率

### 1\.4 输入与输出规范

**输入**：景区名称（中文字符串）

**输入校验规则**（新增）：

- 空输入或非中文 → 提示重新输入

- 无历史背景的景区 → 提示并尝试基于地理特征创作

- 长度限制：2\-20 个字符

**输出**：

- 有声连环画视频（MP4，1080p）

- 附带产物：故事文本、剧本、分镜文档、角色设定图、单页漫画

### 1\.5 异常处理策略（新增）

异常场景 \| 处理策略

景区名称无法识别 \| 回退到通用历史故事模板，提示用户

图像生成失败 \| 重试 3 次，仍失败则使用占位图并标记

TTS 合成失败 \| 跳过该分镜配音，继续后续流程

模型加载超时 \| 超时 60s 自动卸载重试，3 次失败则终止并报错

内存不足 \| 触发紧急卸载，等待 GC 完成后重试

---

## 2\. 架构总览

### 2\.1 整体架构

**架构模式**：Supervisor \+ Sequential Pipeline

系统采用 Supervisor 编排 \+ 顺序流水线架构。核心约束来自 DGX Spark 的 119GB 统一内存：多个大模型无法同时驻留，Supervisor 必须统一调度模型的加载与卸载。

流水线分为四大阶段：

- **Phase 1**：故事生成与剧本创作 — LLM 将景区名称转化为历史故事，再由编剧 skill 改编为剧本，导演 skill 拆分为分镜脚本

- **Phase 2**：角色设计与素材生成 — 根据剧本生成人物小传、道具描述，通过 ComfyUI + Qwen-Image-Edit-2511（+ LoRA）生成角色三视图/两视图

- **Phase 3**：漫画生成与后期 — 基于角色素材和分镜脚本，逐页生成漫画画面，再进行剪辑拼接

- **Phase 4**：配音配乐与最终合成 — CosyVoice2 生成解说语音（ComfyUI 桥接亦可走 Qwen3-TTS），ACE-STEP 1.5 XL 生成背景音乐，最终合成为有声连环画

### 2\.2 NVIDIA 技术栈覆盖

层级 \| 技术 \| 用途

硬件 \| DGX Spark \(119GB 统一内存\) \| 单机承载全部模型推理

LLM 推理 \| Ollama · **glm-4.7-flash:latest**（亦验证过 qwen3.5:122b） \| 故事生成、剧本创作、分镜/角色描述

图像生成 \| ComfyUI · **Qwen-Image-Edit-2511** + Lightning/Real_Ani LoRA；文生图 **Krea2 Turbo** \| 角色三视图、分镜编辑、文生图

图像管线 \| ComfyUI + comfyui-bridge / image-shim \| 工作流编排与 OpenAI 兼容接入

TTS 语音 \| **CosyVoice2-0.5B**（主应用）；Qwen3-TTS VoiceDesign（ComfyUI 桥接可选） \| 分镜解说配音

音乐生成 \| **ACE-STEP 1.5 XL**（ace-step-v1.5xl） \| 背景音乐生成

成片合成 \| FFmpeg \| 有声连环画 MP4

### 2\.3 数据流设计

```Plain Text
景区名称
  ↓ Phase 1: 故事生成
文本故事概要
  ↓ Phase 1: 剧本创作
剧本
  ↓ Phase 1: 分镜脚本
分镜头描述文档
  ↓ Phase 2: 角色设计
人物三视图 + 动物两视图 + 道具描述
  ↓ Phase 3: 漫画生成
单页漫画 × N
  ↓ Phase 3: 漫画剪辑
剪辑后漫画
  ↓ Phase 4: 配音 + 配乐
解说声轨 + 音乐声轨
  ↓ Phase 4: 最终合成
有声连环画 (视频)
```

### 2\.4 Multi\-Agent 架构设计

**Agent 角色定义**

Agent \| 职责 \| 底层模型 \| 内存占用

StoryAgent \| 景区名称 → 历史故事概要 \| glm-4.7-flash（Ollama） \| \~40GB resident

ScriptAgent \| 故事概要 → 剧本 → 分镜脚本 \| 同 LLM + 编剧/导演 skill \| 共用 LLM

CharacterAgent \| 剧本 → 人物小传、道具描述 \| 同 LLM \| 共用 LLM

ImageAgent \| 描述 → 角色三视图/两视图、分镜漫画 \| Qwen-Image-Edit-2511 + LoRA；文生图 Krea2 Turbo（ComfyUI） \| \~40GB 量级

VoiceAgent \| 分镜文本 → 解说语音 \| CosyVoice2-0.5B（主路径） \| 独立 shim :8090

MusicAgent \| 分镜文本 → 背景音乐 \| ACE-STEP 1.5 XL（ComfyUI） \| music-shim :8092

ComposerAgent \| 漫画 \+ 语音 \+ 音乐 → 有声连环画 \| FFmpeg \+ Python \| \~2GB

**Tool\-Augmented Agent 设计**

每个 Agent 配备专用 Tool：

- **StoryAgent Tools**: \`search\_scenic\_history\(name\)\` — 检索景区历史背景；\`generate\_story\(name, style\)\` — 生成故事概要

- **ScriptAgent Tools**: \`generate\_script\(story\)\` — 将故事改编为剧本；\`split\_storyboard\(script\)\` — 将剧本拆分为分镜脚本

- **CharacterAgent Tools**: \`generate\_character\_bio\(script, name\)\` — 生成人物小传及特征描述；\`generate\_prop\_desc\(script, prop\_name\)\` — 生成道具/物品描述

- **ImageAgent Tools**: \`generate\_character\_view\(desc, template\)\` — 生成人物三视图；\`generate\_animal\_view\(desc\)\` — 生成动物两视图；\`generate\_comic\_panel\(views, storyboard\_prompt\)\` — 生成分镜漫画；\`edit\_comic\(panels\)\` — 漫画剪辑

- **VoiceAgent Tools**: \`generate\_narration\(storyboard\_text\)\` — 生成解说语音

- **MusicAgent Tools**: \`generate\_music\(storyboard\_text\)\` — 生成背景音乐

- **ComposerAgent Tools**: \`compose\(comic, voice, music\)\` — 合成有声连环画

### 2\.5 内存编排策略 \(119GB\)

Supervisor 通过 model\_manager\.py 按阶段调度模型加载，确保峰值不超过内存上限：

Stage \| 加载模型 \| 预估内存 \| 操作

Stage 1 \| Ollama glm-4.7-flash \| \~40GB \| 生成故事+剧本+分镜+角色描述 → 保存 JSON（可 keep_alive 或卸载让出显存）

Stage 2 \| ComfyUI（Qwen-Image-Edit-2511 / Krea2） \| \~40GB 量级 \| 生成三视图+漫画+剪辑 → 保存图片

Stage 3 \| CosyVoice2 + ACE-STEP 1.5 XL \| 分时/排队 \| 生成语音+音乐 → 保存音频（共用机上与 ComfyUI 排队）

Stage 4 \| ComposerAgent（FFmpeg） \| \~2GB \| 合成最终视频

> 119GB 统一内存约束下，LLM 与 ComfyUI 大图/音乐任务宜分时；glm-4.7-flash 常驻约 40GB，生成前建议 `ollama ps` 确认。

### 2\.6 Agent 通信协议

所有 Agent 间通过结构化 Message 通信，每条消息持久化为 JSONL：

```Plain Text
Message 格式：
  sender（发送者角色）+ receiver（目标角色）+ msg_type（task/result/control）
  + payload（结构化数据）+ pipeline_id（流水线 ID）+ timestamp
```

---

## 3\. 服务器环境

### 3\.1 SSH 连接信息

```Plain Text
# ~/.ssh/config
Host dgx-spark
  HostName <spark-ip>          # TODO: 填写实际 IP
  User <username>              # TODO: 填写实际用户名
  IdentityFile ~/.ssh/id_rsa
```

> **注意**：SSH 连接信息中的 \`\<spark\-ip\>\` 和 \`\<username\>\` 为占位符，需在 M0 里程碑前确认填写。

### 3\.2 硬件实况

项目 \| 规格

设备 \| NVIDIA DGX Spark

GPU \| 统一内存架构

统一内存 \| 119GB

存储 \| NVMe SSD

OS \| Ubuntu 24.04 LTS（aarch64，团队共用）

> **TODO**：补充 GPU 型号、核心数、显存带宽等详细参数。

### 3.3 本地模型清单（与 DGX / 仓库实际一致）

模型 \| 角色 \| 接入 \| 备注

**glm-4.7-flash:latest** \| 故事/剧本/分镜/角色描述（S0–S2） \| Ollama `:11434` \| 生产默认；亦冒烟验证过 qwen3.5:122b

**Qwen-Image-Edit-2511** + Lightning / Real_Ani LoRA \| 角色三视图、单/双/三图编辑、分镜一致性 \| ComfyUI（`qwen_image_edit_2511_fp8mixed`） \| 工作流见 `comfyui-bridge/workflows/`

**Krea2 Turbo**（`krea2_turbo_mxfp8.safetensors`） \| 文生图 \| ComfyUI \| `Text2IMGKrea2_api.json`

**CosyVoice2-0.5B** \| 分镜解说 TTS（主应用） \| shanhai-tts shim `:8090` \| OpenAI 兼容 `/v1/audio/speech`

**Qwen3-TTS-12Hz-1.7B-VoiceDesign** \| TTS（ComfyUI 桥接可选） \| ComfyUI 工作流 \| `VoiceDesign-QwenTTS.json`

**ACE-STEP 1.5 XL**（`ace-step-v1.5xl`） \| 背景音乐 \| ComfyUI / music-shim `:8092` \| `MusicCreation-ACESTEP1.5XL_api.json`

**模型选型理由**：

- **glm-4.7-flash 做故事/剧本**：Ollama 常驻、体积相对可控（约 40GB resident），适合团队共用机；需要更强推理时可切 qwen3.5:122b
- **Qwen-Image-Edit-2511 + LoRA 做角色一致性**：三视图/参考图注入 + Lightning 加速，支撑分镜跨页外观稳定
- **Krea2 Turbo 做文生图**：高速文生图底图能力，与编辑工作流分工
- **CosyVoice2 做主路径 TTS**：DGX GB10 上已跑通端到端真人声；ComfyUI 侧保留 Qwen3-TTS 作为桥接能力
- **ACE-STEP 1.5 XL 做音乐**：经 music-shim 接入流水线 S5，器乐 BGM 已端到端验证

---

## 4\. 环境搭建

### 4\.1 基础环境

```Plain Text
# Python 环境
python -m venv venv
source venv/bin/activate

# 核心依赖
pip install torch torchvision torchaudio
pip install transformers accelerate
pip install comfyui  # 图像生成管线
pip install ffmpeg-python  # 视频合成

# 实际部署（摘要，详见 docs/guides/ 与 web/docs/deploy-dgx.md）
# LLM：ollama pull glm-4.7-flash:latest
# 图像/音乐权重放入 ComfyUI/models/（Qwen-Image-Edit-2511、Krea2、ACE-STEP 1.5 XL）
# TTS：CosyVoice2-0.5B（ModelScope → shanhai-tts）；可选 Qwen3-TTS（ComfyUI 节点）
```

### 4\.2 项目结构

```Plain Text
scenic-comic/
├── config.py              # 全局配置（模型路径、参数）
├── model_manager.py       # 模型分时加载/卸载管理
├── pipeline.py            # 主流水线编排
├── trace.py               # 通信记录
├── skills/                # Skill 模块
│   ├── story_agent.py     # Phase 1: 故事生成
│   ├── script_agent.py    # Phase 1: 剧本+分镜
│   ├── character_agent.py # Phase 2: 角色设计
│   ├── image_agent.py     # Phase 2-3: 图像生成
│   ├── voice_agent.py     # Phase 4: 配音
│   ├── music_agent.py     # Phase 4: 配乐
│   └── composer_agent.py  # Phase 4: 合成
├── comfyui_workflows/     # ComfyUI 工作流 JSON
│   ├── character_three_view.json
│   ├── animal_two_view.json
│   ├── comic_panel.json
│   └── comic_edit.json
├── outputs/               # 产出目录
│   ├── stories/
│   ├── scripts/
│   ├── characters/
│   ├── comics/
│   ├── audio/
│   └── final/
└── traces/                # 流水线 Trace 日志
```

---

## 5\. Phase 1：故事生成与剧本创作

### 5\.1 模型加载

model\_manager\.py 统一管理模型分时加载/卸载：

```Plain Text
# model_manager.py — 核心机制
# gc.collect() + torch.cuda.empty_cache() 回收显存
# 确保 119GB 统一内存高效利用

class ModelManager:
    def load(self, model_name: str):
        """加载指定模型到显存"""
        ...

    def unload(self, model_name: str):
        """卸载模型，回收显存"""
        gc.collect()
        torch.cuda.empty_cache()
        ...
```

### 5\.2 StoryAgent — 景区故事生成

根据景区名称，自动检索历史背景并生成故事概要。

```Plain Text
# skills/story_agent.py

STORY_SYSTEM_PROMPT = """你是一位历史故事创作专家。
根据给定的景区名称，创作一段引人入胜的历史故事。
要求：
1. 基于真实历史背景，可适当艺术加工
2. 故事要有明确的起承转合
3. 人物形象鲜明，情节生动
4. 适合改编为连环画"""

def generate_story(scenic_name: str, style: str = "历史传奇") -> dict:
    """景区名称 → 文本故事概要"""
    messages = [
        {"role": "system", "content": STORY_SYSTEM_PROMPT},
        {"role": "user", "content": f"景区：{scenic_name}\n风格：{style}"},
    ]
    story = manager.generate("llm", messages, temperature=0.8, max_new_tokens=2048)
    return {"scenic_name": scenic_name, "story": story}
```

### 5\.3 ScriptAgent — 剧本与分镜

将山音编剧大师 skill 和山音导演大师 skill 嵌入 Agent，完成故事→剧本→分镜的两步转换。

```Plain Text
# skills/script_agent.py

def generate_script(story: str) -> dict:
    """故事概要 → 符合要求的剧本"""
    # 调用山音编剧大师 skill
    messages = [
        {"role": "system", "content": SCRIPTWRITER_PROMPT},
        {"role": "user", "content": f"将以下故事改编为连环画剧本：\n{story}"},
    ]
    script = manager.generate("llm", messages, temperature=0.7, max_new_tokens=4096)
    return {"script": script}

def split_storyboard(script: str) -> dict:
    """剧本 → 分镜头描述文档"""
    # 调用山音导演大师 skill
    messages = [
        {"role": "system", "content": DIRECTOR_PROMPT},
        {"role": "user", "content": f"将以下剧本拆分为分镜头脚本：\n{script}"},
    ]
    storyboard = manager.generate("llm", messages, temperature=0.6, max_new_tokens=4096)
    return {"storyboard": storyboard}
```

### 5\.4 CharacterAgent — 角色描述生成

```Plain Text
# skills/character_agent.py

def generate_character_bio(script: str, character_names: list) -> dict:
    """剧本 + 人物名称 → 人物小传、特征描述"""
    messages = [
        {"role": "system", "content": CHARACTER_DESIGNER_PROMPT},
        {"role": "user", "content": f"剧本：{script}\n角色：{character_names}"},
    ]
    bios = manager.generate("llm", messages, temperature=0.7, max_new_tokens=2048)
    return {"character_bios": bios}

def generate_prop_desc(script: str, prop_names: list) -> dict:
    """剧本 + 关键道具名称 → 道具、物品描述"""
    ...
```

---

## 6\. Phase 2：角色设计与素材生成

### 6\.1 ImageAgent — ComfyUI 工作流

使用 ComfyUI 编排 **Qwen-Image-Edit-2511 + LoRA** 做角色/分镜编辑；文生图走 **Krea2 Turbo** 工作流。

**人物三视图生成**：

```Plain Text
# skills/image_agent.py

def generate_character_view(char_desc: str, template: str) -> str:
    """人物特征描述 + 三视图模板 → 人物三视图"""
    workflow = load_workflow("comfyui_workflows/character_three_view.json")
    workflow = inject_params(workflow, {
        "prompt": char_desc,
        "template_image": template,
        "lora_path": LORA_PATH,
    })
    result = comfyui_api.execute(workflow)
    return result["images"][0]["path"]
```

**动物两视图生成**：

```Plain Text
def generate_animal_view(animal_desc: str) -> str:
    """动物特征描述 → 动物两视图"""
    workflow = load_workflow("comfyui_workflows/animal_two_view.json")
    ...
```

### 6\.2 角色一致性策略

21. **LoRA 微调**：针对主要角色微调 LoRA 权重，确保不同分镜中角色外观一致

22. **三视图复用**：先生成角色三视图（正面/侧面/背面），后续分镜以此为参考

23. **Reference Image 注入**：分镜生成时将三视图作为 reference image 注入 ComfyUI 工作流

---

## 7\. Phase 3：漫画生成与后期

### 7\.1 分镜漫画生成

```Plain Text
# skills/image_agent.py — 漫画生成

def generate_comic_panel(character_views: list, storyboard_prompt: str) -> str:
    """多个人物三视图 + 分镜提示词 → 本页漫画"""
    workflow = load_workflow("comfyui_workflows/comic_panel.json")
    workflow = inject_params(workflow, {
        "reference_images": character_views,
        "prompt": storyboard_prompt,
        "lora_path": LORA_PATH,
    })
    result = comfyui_api.execute(workflow)
    return result["images"][0]["path"]
```

### 7\.2 漫画剪辑

```Plain Text
def edit_comic(panels: list) -> str:
    """多个漫画页面 → 剪辑结果"""
    workflow = load_workflow("comfyui_workflows/comic_edit.json")
    ...
```

---

## 8\. Phase 4：配音配乐与最终合成

### 8\.1 VoiceAgent — 分镜配音

使用 **CosyVoice2-0.5B**（主应用 OpenAI 兼容 TTS 端点）生成分镜解说语音：

```Plain Text
# skills/voice_agent.py  — 对应 web 侧 TTSClient → CosyVoice2 shim

def generate_narration(storyboard_text: str) -> str:
    """分镜文本 → 声轨（解说部分）"""
    audio = manager.generate_tts(
        "cosyvoice2",
        text=storyboard_text,
        voice="default",
    )
    return save_audio(audio, "outputs/audio/narration.mp3")
```

> ComfyUI 桥接另提供 Qwen3-TTS VoiceDesign 工作流（`VoiceDesign-QwenTTS.json`），与主应用路径二选一/并行能力。

### 8\.2 MusicAgent — 背景配乐

使用 **ACE-STEP 1.5 XL** 生成背景音乐：

```PlainText
# skills/music_agent.py  — 对应 web 侧 MusicClient → music-shim → ComfyUI

def generate_music(storyboard_text: str) -> str:
    """分镜文本 → 声轨（音乐部分）"""
    audio = manager.generate_music(
        "ace-step-v1.5xl",
        prompt=extract_music_prompt(storyboard_text),  # 纯器乐 lyrics="[instrumental]"
        duration=ESTIMATED_DURATION,
    )
    return save_audio(audio, "outputs/audio/bgm.mp3")
```

### 8\.3 ComposerAgent — 最终合成

```Plain Text
# skills/composer_agent.py

def compose(comic_path: str, voice_path: str, music_path: str) -> str:
    """漫画 + 语音 + 音乐 → 有声连环画"""
    output = "outputs/final/comic_video.mp4"

    # FFmpeg 合成：图片序列 + 语音 + 背景音乐
    cmd = [
        "ffmpeg", "-y",
        "-framerate", "2",          # 每页停留 2 秒
        "-i", comic_path,           # 漫画图片序列
        "-i", voice_path,           # 解说语音
        "-i", music_path,           # 背景音乐
        "-c:v", "libx264",
        "-c:a", "aac",
        "-shortest",
        "-filter:a", "volume=0.3:1", # 背景音乐音量降至 30%，避免盖过解说
        output,
    ]
    subprocess.run(cmd, check=True)
    return output
```

> **优化说明**：新增 \`volume=0\.3\` 滤镜，将背景音乐音量降低至 30%，确保解说语音清晰可辨。

---

## 9\. 完整流水线编排

```Plain Text
# pipeline.py
"""
景区有声连环画全自动流水线
用法: python pipeline.py --scenic "西湖"
"""

def run_pipeline(scenic_name: str):
    # Phase 1: 故事生成与剧本创作
    manager.load("glm-4.7-flash")  # Ollama
    story = generate_story(scenic_name)
    script = generate_script(story["story"])
    storyboard = split_storyboard(script["script"])
    characters = generate_character_bio(script["script"], extract_names(script))
    props = generate_prop_desc(script["script"], extract_props(script))
    manager.unload("glm-4.7-flash")

    # Phase 2: 角色设计
    manager.load("qwen-image-edit-2511")  # ComfyUI
    char_views = [generate_character_view(c, TEMPLATE) for c in characters["bios"]]
    animal_views = [generate_animal_view(a) for a in characters.get("animals", [])]
    manager.unload("qwen-image-edit-2511")

    # Phase 3: 漫画生成（可与 Phase 2 合并一次 ComfyUI 会话）
    manager.load("qwen-image-edit-2511")
    panels = [generate_comic_panel(char_views, sb) for sb in storyboard["panels"]]
    edited = edit_comic(panels)
    manager.unload("qwen-image-edit-2511")

    # Phase 4: 配音配乐与合成
    manager.load("cosyvoice2")
    narration = generate_narration(storyboard["full_text"])
    manager.unload("cosyvoice2")

    manager.load("ace-step-v1.5xl")
    music = generate_music(storyboard["full_text"])
    manager.unload("ace-step-v1.5xl")

    final = compose(edited, narration, music)
    print(f"有声连环画已生成: {final}")
    return final
```

> **优化建议**：Phase 2 和 Phase 3 都走 ComfyUI（Qwen-Image-Edit），可合并为一次加载/排队，减少重复冷启动。

---

## 10\. Demo 展示剧本（3 分钟）

幕次 \| 时长 \| 内容 \| 展示要点

第 1 幕 \| 30s \| \*\*问题引入\*\*："每个景区都有独特的历史故事，但把故事变成有声连环画需要编剧、画师、配音、配乐一整条团队，制作周期长、成本高。我们解决的是：给定一个景区名称，全自动生成有声连环画。" \| 画面：行业痛点数据

第 2 幕 \| 30s \| \*\*输入与故事生成\*\*：现场演示输入"西湖" → StoryAgent 自动生成白蛇传故事概要 → ScriptAgent 改编为剧本，拆分为分镜脚本 \| 画面：实时终端操作 \+ 流水线状态

第 3 幕 \| 60s \| \*\*角色与漫画生成\*\*：CharacterAgent 生成白素贞、许仙、小青的人物小传 → ImageAgent 生成角色三视图 → 逐页生成分镜漫画 \| 画面：三视图 \+ 漫画画面滚动展示

第 4 幕 \| 45s \| \*\*配音配乐与合成\*\*：VoiceAgent 生成解说语音 → MusicAgent 生成背景音乐 → ComposerAgent 合成最终有声连环画 \| 音频：播放语音和音乐片段

第 5 幕 \| 15s \| \*\*成品展示\*\*：播放完整有声连环画片段；展示数据：1 个景区名称 → 12 个模块 → 1 部有声连环画 \| 画面：完整视频播放

---

## 11\. 功能模块清单

序号 \| 功能 \| 负责人 \| 实现方式 \| 所需模型/工具 \| 输入 \| 输出 \| 进度

1 \| 生成景区故事 \| Nancy \| LLM 推理 \| glm-4.7-flash（Ollama） \| 景区名称 \| 文本故事概要 \| 🟢 已跑通

2 \| 生成剧本 \| 轻踏 \| skill 嵌入 Agent \| 山音编剧大师 skill \| 文本故事概要 \| 符合要求的剧本 \| 🔴 未开始

3 \| 生成分镜脚本 \| 轻踏 \| skill 嵌入 Agent \| 山音导演大师 skill \| 剧本 \| 分镜头描述文档 \| 🔴 未开始

4 \| 角色设计\-人物小传 \| 轻踏 \| LLM 推理 \| glm-4.7-flash（Ollama） \| 剧本、人物名称 \| 人物小传、特征描述 \| 🟢 已跑通

5 \| 角色设计\-道具描述 \| 轻踏 \| LLM 推理 \| glm-4.7-flash（Ollama） \| 剧本、道具名称 \| 道具、物品描述 \| 🟢 已跑通

6 \| 角色设计\-人物三视图 \| 五子 \| ComfyUI \| Qwen-Image-Edit-2511 + LoRA \| 人物特征描述\+三视图模板 \| 人物三视图 \| 🟢 已跑通

7 \| 角色设计\-动物两视图 \| 五子 \| ComfyUI \| Qwen-Image-Edit-2511 + LoRA \| 动物特征描述 \| 动物两视图 \| 🟡 能力具备

8 \| 分镜漫画生成 \| 五子 \| ComfyUI \| Qwen-Image-Edit-2511 + LoRA / Krea2 \| 多个人物三视图\+分镜提示词 \| 本页漫画 \| 🟢 已跑通

9 \| 漫画剪辑 \| 五子 \| ComfyUI \| Qwen-Image-Edit-2511（blend/triple） \| 多个漫画 \| 剪辑结果 \| 🟢 工作流已就绪

10 \| 分镜漫画配音 \| 五子 \| TTS 推理 \| CosyVoice2-0.5B（主）；Qwen3-TTS（桥接） \| 分镜文本 \| 声轨\-解说部分 \| 🟢 已跑通

11 \| 分镜漫画配乐 \| 五子 \| 音乐生成 \| ACE-STEP 1.5 XL \| 分镜文本 \| 声轨\-音乐部分 \| 🟢 已跑通

12 \| 漫画合成 \| 待定 \| FFmpeg \| FFmpeg \+ Python \| 单页图文并茂漫画 \| 连环画 \| 🔴 未开始

> **进度图例**：🔴 未开始 \| 🟡 进行中 \| 🟢 已完成

---

## 12\. 团队分工与里程碑

### 12\.1 角色总览

成员 \| 职责 \| 负责模块

Nancy \| 故事生成 \| 模块 1：景区故事生成

轻踏 \| 剧本与角色描述 \| 模块 2\-5：剧本、分镜、人物小传、道具描述

五子 \| 图像与音视频 \| 模块 6\-11：三视图、漫画、配音、配乐

待定 \| 合成与流水线 \| 模块 12：最终合成 \+ pipeline 编排

### 12\.2 关键里程碑

里程碑 \| 时间 \| 交付物 \| 验收标准

M0 \| D1 \| 全员 SSH 连通，模型加载验证通过 \| 四个模型成功加载到内存并可推理

M1 \| D2 \| StoryAgent \+ ScriptAgent 跑通 \| 输入"西湖"→ 输出分镜脚本 JSON

M2 \| D4 \| ImageAgent ComfyUI 工作流就绪 \| 成功生成至少 1 个角色三视图

M3 \| D6 \| 分镜漫画生成跑通 \| 至少 3 页漫画，角色相似度 ≥ 85%

M4 \| D8 \| VoiceAgent \+ MusicAgent 跑通 \| 生成完整解说 \+ 背景音乐

M5 \| D9 \| 全链路 pipeline 跑通 \| 端到端产出第一版有声连环画

M6 \| D10 \| Demo 就绪 \| 3 分钟现场演示可跑通，产出质量可接受

> **优化说明**：为每个里程碑增加了明确的「验收标准」，确保团队对"完成"的定义一致。

---

## 13\. 讨论与开放问题

### 13.1 模型部署状态

模型 \| 状态 \| 备注

glm-4.7-flash:latest（Ollama） \| 🟢 已部署 \| DGX 生产默认；开机可预加载

Qwen-Image-Edit-2511 + LoRA \| 🟢 已部署 \| ComfyUI 工作流已入库

Krea2 Turbo \| 🟢 已部署 \| 文生图工作流已入库

CosyVoice2-0.5B \| 🟢 已部署 \| shanhai-tts.service

Qwen3-TTS VoiceDesign \| 🟢 已部署（桥接） \| ComfyUI 可选路径

ACE-STEP 1.5 XL \| 🟢 已部署 \| music-shim → ComfyUI

### 13\.2 角色一致性策略

**当前方案**：LoRA 微调 \+ 三视图复用 \+ Reference Image 注入

**待验证问题**：

24. LoRA 训练数据量需要多少？是否可以用少量样本（5\-10 张）快速微调？

25. 不同分镜角度（俯视、仰视）下角色一致性如何保障？

26. 多角色同框时如何分别保持各自一致性？

### 13\.3 TRT\-LLM 接入策略（扩展优化）

优先级 \| 模型 \| 理由

P1 \| Qwen-Image-Edit-2511 / Krea2（ImageAgent） \| 图像生成调用最频繁，推理延迟下降收益大

P2 \| glm-4.7-flash（Ollama） \| 可优先启用原生 Ollama 适配器（think:false）提速，再考虑 TRT-LLM

P3（可选） \| CosyVoice2 / ACE-STEP 1.5 XL \| 非主瓶颈，时间紧可跳过

### 13\.4 剩余 TODO 清单

优先级 \| 任务 \| 负责人 \| 状态

P0 \| 下载模型到 Spark，四个模型目录确认 \| \- \| 🟢 已完成（见 §13.1）

P0 \| pipeline 端到端冒烟测试 \| \- \| 🟢 DGX 已跑通 S0–S6

P0 \| 确认 SSH 连接信息（IP、用户名） \| \- \| 🟡 内网已通，文档占位符待补全

P0 \| 统一模型版本号描述（与本仓库实际一致） \| \- \| 🟢 v2.1 已对齐

P1 \| ComfyUI 工作流 JSON 编写（三视图、两视图、分镜、剪辑） \| 五子 \| 🟢 已入库 comfyui-bridge/workflows/

P1 \| LoRA 训练数据准备 \+ 微调脚本 \| 五子 \| 🔴

P1 \| 山音编剧大师 / 导演大师 skill 接入验证 \| 轻踏 \| 🔴

P1 \| 确认模块 12 负责人 \| \- \| 🔴

P1 \| FFmpeg 音量混合参数调优 \| \- \| 🔴

P2 \| config\.py 完善，统一参数管理 \| \- \| 🔴

P2 \| 角色一致性效果评估方案 \| 五子 \| 🔴

P2 \| Skill Router 路由层设计（D6 实现） \| \- \| 🔴

P2 \| 异常处理与重试机制实现 \| \- \| 🔴

P2 \| 输入校验逻辑实现 \| \- \| 🔴

---

## 14\. 产品评审检查清单（新增）

### 14\.1 原文档问题与优化对照

\# \| 问题描述 \| 问题类型 \| 优化措施

1 \| 标题重复出现两次 \| 格式 \| 删除重复标题

2 \| 缺少目标用户定义 \| 产品 \| 新增 §1\.1 目标用户

3 \| 缺少 User Story \| 需求 \| 新增 §1\.2 核心 User Story

4 \| 缺少成功指标/SLA \| 产品 \| 新增 §1\.3 成功指标

5 \| 缺少输入校验规范 \| 产品 \| 新增 §1\.4 输入输出规范

6 \| 缺少异常处理策略 \| 设计 \| 新增 §1\.5 异常处理策略

7 \| LLM 版本号不统一 \| 内容 \| v2.1 已统一为 glm-4.7-flash:latest（Ollama）

8 \| SSH 信息为占位符未标注 TODO \| 内容 \| 明确标注 TODO

9 \| 模块清单"进度"列为空 \| 管理 \| 补充进度状态（🔴 未开始）

10 \| 里程碑缺少验收标准 \| 管理 \| 新增验收标准列

11 \| 模块 12 负责人为"待定"未跟踪 \| 管理 \| 加入 TODO 清单

12 \| FFmpeg 合成缺少音量混合策略 \| 技术 \| 新增 volume 滤镜参数

13 \| Phase 2/3 重复加载 ComfyUI 图像模型 \| 优化 \| 标注优化建议

14 \| 缺少文档目录 \| 格式 \| 新增目录

15 \| 缺少文档版本信息 \| 格式 \| 新增版本信息

16 \| Demo 剧本缺少结构化表格 \| 展示 \| 改为表格形式

### 14\.2 后续建议

27. **用户调研**：在 D5 前找 2\-3 个景区运营方做简单访谈，验证产品方向

28. **竞品分析**：整理 2\-3 个同类 AI 内容生成方案的对比表（建议产品经理牵头）

29. **输出格式扩展**：考虑支持 PDF 漫画册、纯音频播客等衍生输出

30. **多语言支持**：规划英文版输出，扩大适用场景

31. **缓存机制**：对同一景区的结果做缓存，避免重复计算

32. **用户反馈闭环**：在输出中嵌入评分机制，收集质量反馈用于优化

---

> 📝 本文档由产品经理审阅优化，如有建议请随时讨论。

