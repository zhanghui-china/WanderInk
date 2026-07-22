# WanderInk

## 📖 Project Introduction

**Project Naming:**

WanderInk - Scenic Spot Audio Comic Agent, Chinese name "漫游墨绘" (Man You Mo Hui). "Wander" implies roaming and exploration, while "Ink" symbolizes brush and ink painting. The Chinese name "漫游墨绘" carries both the artistic conception of exploration and traditional cultural charm.

**Project Content:**

WanderInk is a multimodal creative project focused on scenic spot cultural IP development. It builds a complete closed loop around "Scenic Spot Name → Historical Story → Script Creation → Storyboard → Character Design → Comic Generation → Voiceover & Music → Audio Comic".

**Project Background and Motivation:**

- **Industry Pain Points:** Scenic spot cultural IP development heavily relies on manual creative teams (screenwriters, artists, voice actors, composers), resulting in long production cycles and high costs that small and medium scenic spots cannot afford;
- **Limitations of Existing AI Solutions:** General LLMs can only generate text, image generation models lack character consistency, TTS/music generation is disconnected from visuals, and there is no end-to-end fully automated solution;
- **Opportunity:** NVIDIA DGX Spark's 128GB unified memory enables multi-model collaborative inference, compressing story generation, image generation, speech synthesis, and music generation into a single-machine pipeline, achieving fully automated "scenic spot name in, audio comic out".

This repository contains ComfyUI-based image generation pipelines and API services, implementing fully automated end-to-end generation from text to images and audio.

- **End-to-End Full Automation:** From scenic spot name to final audio comic, all functional modules are automatically connected without manual intervention;
- **Character Consistency:** Through Qwen-Image-Edit-2511 + LoRA fine-tuning, character appearance consistency is maintained during storyboard comic generation (three-view → storyboard reuse);
- **Multi-Model Time-Slice Scheduling:** Under DGX Spark unified memory constraints, LLM, image editing model, TTS model, and music generation model are loaded in turns, with peak memory not exceeding limits;
- **NVIDIA Full-Stack Deployment:** DGX Spark unified memory + StepFun series (LLM/TTS) + ACE-STEP music generation + ComfyUI image pipeline, showcasing the complete creative AI lifecycle;
- **Skill Modular Design:** Some functional modules are abstracted as independent Skills (script: screenwriter skill, storyboard: director skill, etc.), supporting flexible expansion to new scenarios.

> Detailed product documentation: [docs/product/](docs/product/)

## Demo

> [Demo Video (bilibili)](https://www.bilibili.com/video/BV1ymK865Et1)
>
> [Demo PPT](https://github.com/zhanghui-china/WanderInk/blob/main/docs/demo/Wanderink%20%E6%99%AF%E5%8C%BA%E6%9C%89%E5%A3%B0%E8%BF%9E%E7%8E%AF%E7%94%BB%20Agent.pptx)

## One-Sentence Pitch

**Scenic spot name in → Audio Comic (MP4) out**, end-to-end automation with step-by-step preview and regeneration support.

## Pipeline (S0–S6)

```
Input: Scenic Spot Name
  → [S0] LEGEND Legend Retrieval & Verification
  → [S1] SCRIPT Script Adaptation
  → [S2] BOARD Storyboard Design
  → [S3] ROLE Character Design (three-view, lock appearance consistency)
  → [S4] PAGES Comic Page Generation
  → [S5] VOICE Voiceover + MUSIC Background Music
  → [S6] FILM Composite Output MP4
```

## 🗺️ Technical Architecture

This project is specifically designed for **NVIDIA DGX Spark (GB10 128G Unified Memory)**. It adopts a **"Multi-Model Time-Slice Scheduling"** architecture.

### Agent Role Definitions

| Agent          | Responsibility | Core Capabilities            |
| -------------- | -------------- | ---------------------------- |
| StoryAgent     | Scenic story generation | Retrieve historical background, generate story summary |
| ScriptAgent    | Script & storyboard | Story adaptation, storyboard splitting |
| CharacterAgent | Character description generation | Character biography, prop description |
| ImageAgent     | Image generation | Character three-view, storyboard comics, image editing |
| VoiceAgent     | Storyboard voiceover | Qwen3-TTS speech synthesis |
| MusicAgent     | Background music | ACE-STEP music generation |
| ComposerAgent  | Final composition | Comic + voice + music composition |

### Technology Stack

- **Text Model:** Sehyo-Qwen3.5-35B-A3B-NVFP4 (local), Step-3.7-Flash (cloud): <https://modelscope.cn/models/hf/Sehyo-Qwen3.5-35B-A3B-NVFP4>
- **Image Editing Model:** Qwen-Image-Edit-2511 (local), openAI GPT Image2 (cloud): <https://modelscope.cn/models/Qwen/Qwen-Image-Edit-2511>
- **Speech Synthesis Model:** Qwen3-TTS: <https://modelscope.cn/models/Qwen/Qwen3-TTS-12Hz-1.7B-Base>
- **Music Generation Model:** ACE-STEP XL Turbo (ACE Studio & StepFun): <https://modelscope.cn/models/ACE-Step/acestep-v15-xl-turbo>
- **Framework:** ComfyUI, FastAPI + Pydantic + ThreadPoolExecutor, React 18 + Vite 5 + Tailwind CSS + TypeScript

## 🚀 Quick Start

### 1. Environment Requirements

- **Hardware:** NVIDIA DGX Spark (GB10 128G Unified Memory) or equivalent GPU
- **Operating System:** Ubuntu 24.04
- **Python:** 3.12+ (Anaconda)

### 2. System Installation

#### 2.1 Install ComfyUI Environment

```bash
# Login as wuzi user
source ~/.bashrc

# Install dependencies
sudo apt-get install -y sox libsox-fmt-all

# Create conda environment
conda create -n comfyui python=3.12 -y
conda activate comfyui

# Download ComfyUI repository
cd ~
git clone https://github.com/comfyanonymous/ComfyUI.git

# Install PyTorch based on GPU CUDA version
pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130

# Install ComfyUI dependencies
cd ~/ComfyUI
pip install -r requirements.txt
```

Edit `~/.config/systemd/user/comfyui.service` to configure systemd service:

```
[Unit]
Description=ComfyUI User Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/home1/wuzi/ComfyUI
Environment="HF_ENDPOINT=https://hf-mirror.com"
ExecStart=/home1/wuzi/anaconda3/bin/conda run --no-capture-output -n comfyui python main.py --listen 0.0.0.0 --port 8188 --use-flash-attention --gpu-only
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

#### 2.2 Install Web Service Environment

```
# Login as huntun user
source ~/.bashrc

# Download repository
git clone https://github.com/zhanghui-china/WanderInk 
cd ~/WanderInk/web

# Fill in endpoints and models according to environment
cp .env.example .env   

# Create uv environment
uv sync
uv run shanhai-web

# Install web environment
cd web/web
bun install && bun run dev
```

Edit `~/.config/systemd/user/shanhai-web.service` to configure systemd service:

```
[Unit]
Description=shanhai web (FastAPI + SPA)
After=network-online.target
Wants=network-online.target
RequiresMountsFor=%h/shanhai

[Service]
WorkingDirectory=%h/shanhai
EnvironmentFile=%h/shanhai/.env
Environment="PATH=%h/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin"
ExecStart=%h/.local/bin/uv run shanhai-web
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
```

Edit `~/.config/systemd/user/shanhai-image.service` to configure systemd service:

```
[Unit]
Description=shanhai image shim (ComfyUI, OpenAI-compatible)
After=network-online.target
Wants=network-online.target
RequiresMountsFor=%h/image-shim

[Service]
WorkingDirectory=%h/image-shim
Environment="PATH=%h/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=%h/image-shim/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8091
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

Edit `~/.config/systemd/user/shanhai-tts.service` to configure systemd service:

```
[Unit]
Description=shanhai TTS shim (Qwen3-TTS VoiceDesign via ComfyUI, OpenAI-compatible)
After=network-online.target
Wants=network-online.target
RequiresMountsFor=%h/qwentts-shim

[Service]
WorkingDirectory=%h/qwentts-shim
Environment="PATH=%h/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=%h/qwentts-shim/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8090
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

Edit `~/.config/systemd/user/shanhai-music.service` to configure systemd service:

```
[Unit]
Description=shanhai music shim (ACE-Step via ComfyUI, OpenAI-compatible)
After=network-online.target
Wants=network-online.target
RequiresMountsFor=%h/music-shim

[Service]
WorkingDirectory=%h/music-shim
Environment="PATH=%h/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=%h/music-shim/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8092
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

#### 2.3 Install Hermes Environment

Refer to <https://zhuanlan.zhihu.com/p/2056830749530142643>

For security reasons, the Hermes environment is isolated from the WanderInk runtime environment to prevent Hermes from deleting project code and documents after gaining high permissions.

```
# Login as hermes user
source ~/.bashrc

# Install dependencies
sudo apt install ripgrep

# Install hermes
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash

# Configure hermes
hermes setup
```

#### 2.4 Model Download

##### 2.4.1 Download LLM Model: Sehyo-Qwen3.5-35B-A3B-NVFP4

Edit `download_model_by_modelscope_Sehyo-Qwen3.5-35B-A3B-NVFP4.py`:

```python
from modelscope import snapshot_download
import os

model_id = "hf/Sehyo-Qwen3.5-35B-A3B-NVFP4"
local_dir = "/home1/wuzi/models/"

model_dir = snapshot_download(
    model_id,
    local_dir=local_dir,
    revision='master'
)

print(f"Download completed, files saved to: {model_dir}")
```

Download the model:

```bash
python download_model_by_modelscope_Sehyo-Qwen3.5-35B-A3B-NVFP4.py
```

##### 2.4.2 Download ComfyUI Image Editing Model: Qwen-Image-Edit-2511

> [!NOTE]
> The system's `~/ComfyUI/models` directory is a symbolic link pointing to the actual storage directory `~/models/comfyui_models`. The following operations download model files directly to the real directory.

Download `qwen_image_edit_2511_fp8mixed.safetensors` and save to `~/models/comfyui_models/diffusion_models/`.

Choose one of the following methods:

- **Using wget (recommended, with domestic mirror acceleration):**
  ```bash
  export HF_ENDPOINT=https://hf-mirror.com
  huggingface-cli download Comfy-Org/Qwen-Image-Edit_ComfyUI split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors --local-dir ~/models/comfyui_models/diffusion_models --local-dir-use-symlinks False

  mv ~/models/comfyui_models/diffusion_models/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors ~/models/comfyui_models/diffusion_models/
  rm -rf ~/models/comfyui_models/diffusion_models/split_files
  ```
- **Using huggingface-cli:**
  ```bash
  mkdir -p ~/models/comfyui_models/diffusion_models

  wget -O ~/models/comfyui_models/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors \
    https://hf-mirror.com/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors
  ```

##### 2.4.3 Download ComfyUI Speech Synthesis Model: Qwen3-TTS

Download the complete `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` repository and save to `~/models/comfyui_models/qwen-tts/Qwen3-TTS-12Hz-1.7B-VoiceDesign`.

Choose one of the following methods:

- **Using huggingface-cli (recommended, with domestic mirror acceleration):**
  ```bash
  mkdir -p ~/models/comfyui_models/qwen-tts/Qwen3-TTS-12Hz-1.7B-VoiceDesign

  export HF_ENDPOINT=https://hf-mirror.com
  huggingface-cli download Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign \
    --local-dir ~/models/comfyui_models/qwen-tts/Qwen3-TTS-12Hz-1.7B-VoiceDesign \
    --local-dir-use-symlinks False
  ```

* **Using git clone (requires git-lfs installed):**
  ```bash
  git clone https://hf-mirror.com/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign ~/models/comfyui_models/qwen-tts/Qwen3-TTS-12Hz-1.7B-VoiceDesign
  ```
* **Official Hugging Face link:**
  [Qwen3-TTS-12Hz-1.7B-VoiceDesign](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign/tree/main)

##### 2.4.4 Download ComfyUI Music Generation Model: ACE-STEP XL Turbo

Download `acestep1.5XL_ComfyUI_aio-marduk191.safetensors` and save to `~/models/comfyui_models/checkpoints/`.

Choose one of the following methods:

- **Using wget (recommended, with domestic mirror acceleration):**
  ```bash
  export HF_ENDPOINT=https://hf-mirror.com
  huggingface-cli download marduk191/acestep1.5XL_ComfyUI_aio-marduk191 acestep1.5XL_ComfyUI_aio-marduk191.safetensors --local-dir ~/models/comfyui_models/checkpoints --local-dir-use-symlinks False
  ```
- **Using huggingface-cli:**
  ```bash
  mkdir -p ~/models/comfyui_models/vae

  wget -O ~/models/comfyui_models/vae/qwen_image_vae.safetensors \
    https://hf-mirror.com/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors
  ```
- **Official Hugging Face link:**
  [acestep1.5XL_ComfyUI_aio-marduk191.safetensors](https://huggingface.co/marduk191/acestep1.5XL_ComfyUI_aio-marduk191/tree/main)

##### 2.4.5 Download ComfyUI VAE Model

Download `qwen_image_vae.safetensors` and save to `~/models/comfyui_models/vae/`.

Choose one of the following methods:

- **Using wget (recommended, with domestic mirror acceleration):**
  ```bash
  export HF_ENDPOINT=https://hf-mirror.com
  huggingface-cli download marduk191/acestep1.5XL_ComfyUI_aio-marduk191 acestep1.5XL_ComfyUI_aio-marduk191.safetensors --local-dir ~/models/comfyui_models/checkpoints --local-dir-use-symlinks False
  ```
- **Official Hugging Face link:**
  [qwen_image_vae.safetensors](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/blob/main/split_files/vae/qwen_image_vae.safetensors)

##### 2.4.6 Download ComfyUI CLIP/Text Encoder Model

Download `qwen_2.5_vl_7b.safetensors` and `qwen3vl_4b_bf16.safetensors` and save to `~/models/comfyui_models/text_encoders/`.

Choose one of the following methods:

- **Using wget (recommended, with domestic mirror acceleration):**

```bash
mkdir -p ~/models/comfyui_models/text_encoders

wget -O ~/models/comfyui_models/text_encoders/qwen_2.5_vl_7b.safetensors \
  https://hf-mirror.com/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b.safetensors

wget -O ~/models/comfyui_models/text_encoders/qwen3vl_4b_bf16.safetensors \
  https://hf-mirror.com/Comfy-Org/Krea-2/resolve/main/text_encoders/qwen3vl_4b_bf16.safetensors
```

- **Using huggingface-cli:**
  ```bash
  mkdir -p ~/models/comfyui_models/checkpoints

  wget -O ~/models/comfyui_models/checkpoints/acestep1.5XL_ComfyUI_aio-marduk191.safetensors \
    https://hf-mirror.com/marduk191/acestep1.5XL_ComfyUI_aio-marduk191/resolve/main/acestep1.5XL_ComfyUI_aio-marduk191.safetensors
  ```
- **Official Hugging Face links:**
  - [qwen_2.5_vl_7b.safetensors](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/blob/main/split_files/text_encoders/qwen_2.5_vl_7b.safetensors)
  - [qwen3vl_4b_bf16.safetensors](https://huggingface.co/Comfy-Org/Krea-2/blob/main/text_encoders/qwen3vl_4b_bf16.safetensors)

##### 2.4.7 Download ComfyUI LoRA Model

###### 2.4.7.1 Qwen-Image-Edit-2511

Download `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` and save to `~/models/comfyui_models/loras/`.

Choose one of the following methods:

- **Using wget (recommended, with domestic mirror acceleration):**
  ```bash
  mkdir -p ~/models/comfyui_models/loras

  wget -O ~/models/comfyui_models/loras/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors \
    https://hf-mirror.com/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/main/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors
  ```
- **Official Hugging Face link:**
  [Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning/blob/main/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors)

###### 2.4.7.2 Other LoRA Models

Other LoRA models can be downloaded from civital.com and saved to `~/models/comfyui_models/loras/`.

- **LoRA Model Links:**
  - [Real_ani_qwen](https://civitai.com/models/2164588/gen-ani-art-style-qwen-lora)
  - [Qwen-image_2511_Edit_Ball-jointed_Doll V2.0](https://civitai.com/models/2303022/qwen-image2511editball-jointeddoll-v20)
  - [Nano banana figurine style](https://civitai.com/models/1900696/nano-banana-figurine-style-qwen-image-edit)

#### 2.5 Download vLLM Docker Image

```bash
docker pull vllm/vllm-openai:cu130-nightly
```

### 3. System Startup

#### 3.1 Start ComfyUI Service

```bash
# Login as wuzi user
source ~/.bashrc

# Start ComfyUI service
systemctl --user restart comfyui
systemctl --user status comfyui
```

The service will start on port `8188`.

```
(comfyui) wuzi@gx10-8e22:~$ systemctl --user status comfyui
● comfyui.service - ComfyUI User Service
     Loaded: loaded (/home1/wuzi/.config/systemd/user/comfyui.service; enabled; preset: enabled)
     Active: active (running) since Sat 2026-07-18 05:08:30 UTC; 11h ago
   Main PID: 86108 (conda)
      Tasks: 88 (limit: 153553)
     Memory: 8.6G (peak: 38.6G swap: 2.4G swap peak: 2.8G)
        CPU: 1h 37min 33.782s
     CGroup: /user.slice/user-1002.slice/user@1002.service/app.slice/comfyui.service
             ├─86108 /home1/wuzi/anaconda3/bin/python /home1/wuzi/anaconda3/bin/conda run --no-capture-output -n comfyui python main.py --listen 0.0.0.0 --por
             ├─86109 /usr/bin/bash /tmp/tmptj92d21u
             └─86116 python main.py --listen 0.0.0.0 --port 8188 --use-flash-attention --gpu-only

Jul 18 16:24:42 gx10-8e22 conda[86116]: [INFO] Prompt executed in 57.45 seconds
Jul 18 16:24:44 gx10-8e22 conda[86116]: [INFO] got prompt
Jul 18 16:25:38 gx10-8e22 conda[86116]: [394B blob data]
Jul 18 16:25:41 gx10-8e22 conda[86116]: [INFO] Prompt executed in 57.30 seconds
Jul 18 16:25:42 gx10-8e22 conda[86116]: [INFO] got prompt
Jul 18 16:26:37 gx10-8e22 conda[86116]: [394B blob data]
Jul 18 16:26:40 gx10-8e22 conda[86116]: [INFO] Prompt executed in 57.44 seconds
Jul 18 16:26:41 gx10-8e22 conda[86116]: [INFO] got prompt
Jul 18 16:27:07 gx10-8e22 conda[86116]: [394B blob data]
Jul 18 16:27:09 gx10-8e22 conda[86116]: [INFO] Prompt executed in 28.26 seconds
```

#### 3.2 Start Web Service

```bash
# Login as huntun user
source ~/.bashrc

# Start Web, TTS, Image, and Music services
systemctl --user start shanhai-web shanhai-tts shanhai-image shanhai-music

# Check service status
systemctl --user status shanhai-web shanhai-tts shanhai-image shanhai-music
```

Services will start on ports `8090, 8091, 8092`.

```
(base) huntun@gx10-8e22:~$ systemctl --user status shanhai-web shanhai-tts shanhai-image shanhai-music
● shanhai-web.service - shanhai web (FastAPI + SPA)
     Loaded: loaded (/home1/huntun/.config/systemd/user/shanhai-web.service; enabled; preset: enabled)
     Active: active (running) since Sat 2026-07-18 08:45:35 UTC; 7h ago
   Main PID: 98952 (uv)
      Tasks: 11 (limit: 153553)
     Memory: 320.9M (peak: 7.4G swap: 33.3M swap peak: 33.3M)
        CPU: 16min 18.501s
     CGroup: /user.slice/user-1007.slice/user@1007.service/app.slice/shanhai-web.service
             ├─98952 /home1/huntun/.local/bin/uv run shanhai-web
             └─98963 /home1/huntun/shanhai/.venv/bin/python3 /home1/huntun/shanhai/.venv/bin/shanhai-web

Jul 18 16:20:40 gx10-8e22 uv[98963]: INFO:     192.168.199.160:12451 - "GET /api/queue HTTP/1.1" 200 OK

● shanhai-tts.service - shanhai TTS shim (Qwen3-TTS VoiceDesign via ComfyUI, OpenAI-compatible)
     Loaded: loaded (/home1/huntun/.config/systemd/user/shanhai-tts.service; enabled; preset: enabled)
     Active: active (running) since Sat 2026-07-18 08:45:35 UTC; 7h ago
   Main PID: 98953 (uvicorn)
      Tasks: 1 (limit: 153553)
     Memory: 38.5M (peak: 42.1M swap: 16.6M swap peak: 16.6M)
        CPU: 42.400s
     CGroup: /user.slice/user-1007.slice/user@1007.service/app.slice/shanhai-tts.service
             └─98953 /home1/huntun/qwentts-shim/.venv/bin/python3 /home1/huntun/qwentts-shim/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8090

Jul 18 09:30:16 gx10-8e22 uvicorn[98953]: INFO:     127.0.0.1:57484 - "POST /v1/audio/speech HTTP/1.1" 200 OK


● shanhai-image.service - shanhai image shim (ComfyUI, OpenAI-compatible)
     Loaded: loaded (/home1/huntun/.config/systemd/user/shanhai-image.service; enabled; preset: enabled)
     Active: active (running) since Sat 2026-07-18 08:45:35 UTC; 7h ago
   Main PID: 98954 (uvicorn)
      Tasks: 6 (limit: 153553)
     Memory: 42.3M (peak: 60.3M swap: 9.4M swap peak: 10.5M)
        CPU: 41.412s
     CGroup: /user.slice/user-1007.slice/user@1007.service/app.slice/shanhai-image.service
             └─98954 /home1/huntun/image-shim/.venv/bin/python3 /home1/huntun/image-shim/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8091

Jul 18 16:21:49 gx10-8e22 uvicorn[98954]: INFO:     127.0.0.1:51546 - "POST /v1/images/edits HTTP/1.1" 200 OK

● shanhai-music.service - shanhai music shim (ACE-Step via ComfyUI, OpenAI-compatible)
     Loaded: loaded (/home1/huntun/.config/systemd/user/shanhai-music.service; enabled; preset: enabled)
     Active: active (running) since Sat 2026-07-18 08:45:35 UTC; 7h ago
   Main PID: 98955 (uvicorn)
      Tasks: 1 (limit: 153553)
     Memory: 15.7M (peak: 37.7M swap: 21.3M swap peak: 21.3M)
        CPU: 40.654s
     CGroup: /user.slice/user-1007.slice/user@1007.service/app.slice/shanhai-music.service
             └─98955 /home1/huntun/music-shim/.venv/bin/python /home1/huntun/music-shim/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8092

Jul 18 08:45:35 gx10-8e22 systemd[2249]: Started shanhai-music.service - shanhai music shim (ACE-Step via ComfyUI, OpenAI-compatible).
Jul 18 08:45:35 gx10-8e22 uvicorn[98955]: INFO:     Started server process [98955]
Jul 18 08:45:35 gx10-8e22 uvicorn[98955]: INFO:     Waiting for application startup.
Jul 18 08:45:35 gx10-8e22 uvicorn[98955]: INFO:     Application startup complete.
Jul 18 08:45:35 gx10-8e22 uvicorn[98955]: INFO:     Uvicorn running on http://127.0.0.1:8092 (Press CTRL+C to quit)
(base) huntun@gx10-8e22:~$
```

#### 3.3 Start Hermes Service

```bash
# Login as hermes user
source ~/.bashrc

hermes gateway restart
```

#### 3.4 LLM Model Startup

Edit file: `/home1/wuzi/models/Sehyo/Qwen3.5-35B-A3B-NVFP4/model_qwen35_p8000.yaml`

```yaml
host: "0.0.0.0"
port: 8000
reasoning-parser: "qwen3"
enable-auto-tool-choice: true
tool-call-parser: "qwen3_xml"
dtype: auto
max-model-len: 128K
api-key: "sk-my-api-key"
disable-custom-all-reduce: true
generation-config: "vllm"
gpu-memory-utilization: 0.3
language-model-only: true
```

Edit file: `/home1/wuzi/docker/docker_start_Sehyo-Qwen3.5-35B-A3B-NVFP4.sh`

```bash
docker run -d --gpus all --rm \
       -v /home1/wuzi/models/Sehyo/:/mnt/ \
       -p 0.0.0.0:8000:8000/tcp \
       --name qwen35_35b_a3b vllm/vllm-openai:cu130-nightly /mnt/Qwen3.5-35B-A3B-NVFP4 \
       --served-model-name DGX-Qwen3.5-35B-A3B \
       --api_key "sk-my-api-key" \
       --config /mnt/Qwen3.5-35B-A3B-NVFP4/model_qwen35_p8000.yaml \
       --speculative-config '{"method": "mtp", "num_speculative_tokens": 1}'
```

Start Sehyo-Qwen3.5-35B-A3B-NVFP4 using Docker:

```bash
cd /home1/wuzi/docker
chmod +x docker_start_Sehyo-Qwen3.5-35B-A3B-NVFP4.sh
sh ./docker_start_Sehyo-Qwen3.5-35B-A3B-NVFP4.sh
docker ps
docker logs [containerid]
```

## ✨ Project Report

[Project Report (Chinese)](https://github.com/zhanghui-china/WanderInk/blob/main/docs/%E9%A1%B9%E7%9B%AE%E8%AF%B4%E6%98%8E%E6%96%87%E6%A1%A3.md) | [Project Report (English)](https://github.com/zhanghui-china/WanderInk/blob/main/docs/%E9%A1%B9%E7%9B%AE%E8%AF%B4%E6%98%8E%E6%96%87%E6%A1%A3_en.md)

## 📋 Project Code Structure

```
WanderInk/
├── comfyui-bridge/       # ComfyUI HTTP bridge service
│   ├── workflows/        # Image / TTS / Music JSON workflow templates
│   ├── test/             # CLI test scripts
│   ├── comfyui_api_service.py
├── docs/                 # Project documentation
│   ├── product/          # Product documentation
│   ├── guides/           # DGX / Ollama / ComfyUI operation guides
│   ├── demo/             # (Optional) Place demo video files
│   ├── Web技术白皮书.md   # Web Technical White Paper
│   ├── Web端操作手册.md   # Web User Manual
│   └── 项目说明文档.md    # Project Description Document
├── samples/              # Generated work samples
│   ├── mp4/              # Audio comic videos
│   ├── pdf/              # PDF comic books
│   ├── png/              # Comic page images
│   └── opr/              # Operation manual screenshots
└── web/                  # Main application (FastAPI + React)
    ├── src/shanhai/      # Backend pipeline S0–S6
    │   ├── providers/    # Provider layer (LLM/Image/TTS/Music)
    │   ├── steps/        # Stage implementations (S0-S6)
    │   ├── api.py        # FastAPI interface definitions
    │   ├── cli.py        # Command-line tools
    │   ├── config.py     # Configuration management
    │   ├── store.py      # Data persistence
    │   └── schema.py     # Data model definitions
    ├── web/              # Frontend (React + Vite + Tailwind)
    │   ├── src/
    │   │   ├── components/  # React components
    │   │   ├── data/        # Static data
    │   │   └── api.ts       # API client
    │   └── vite.config.ts
    ├── assets/           # Static assets (fonts, BGM, etc.)
    ├── docs/             # PRD, decision records, specifications
    │   ├── decisions/    # Decision records (0001-0006)
    │   └── superpowers/  # Plans and specifications
    ├── tests/            # Unit tests (300+ cases)
    ├── scripts/          # Helper scripts
    ├── spike/            # Experimental code
    └── .env.example      # Environment variable template

```

## Document Index

| Location                                                                 | Content                          |
| ------------------------------------------------------------------------ | ------------------------------   |
| [docs/项目说明文档.md](docs/项目说明文档.md)                             | Project Description Document (Chinese) |
| [docs/项目说明文档_en.md](docs/项目说明文档_en.md)                       | Project Description Document (English) |
| [docs/product/](docs/product/)                                           | Product documentation (optimized version) |
| [docs/guides/](docs/guides/)                                             | ComfyUI / Ollama / DGX operation guides |
| [docs/Web技术白皮书.md](docs/Web技术白皮书.md)                           | Web Technical White Paper       |
| [docs/Web端操作手册.md](docs/Web端操作手册.md)                           | Web User Manual                 |
| [web/docs/](web/docs/)                                                   | PRD, decision records            |
| [web/docs/deploy-dgx.md](web/docs/deploy-dgx.md)                         | DGX deployment instructions      |
| [web/.env.example](web/.env.example)                                     | Environment variable template    |
| [comfyui-bridge/README.md](comfyui-bridge/README.md)                     | Bridge service documentation     |

## 📆 Updates and Team Activities

[2026.7.21] **Zhang Xiaobai** wrote the WanderInk Experience Manual: https://zhuanlan.zhihu.com/p/2062795404199073593. **Nancy** created the DEMO explanation video: https://www.bilibili.com/video/BV1ymK865Et1. **Bandu Wuzi** created the team photo.

[2026.7.20] **Nancy** created the project presentation PPT.

[2026.7.19] **Zhang Xiaobai**, **Bandu Wuzi** improved deployment documentation, project introduction documentation, etc.

[2026.7.18] Project members conducted a series of test verifications. **Huntun** fixed Web bugs and the Web code was finalized.

[2026.7.15] Project members conducted intensive testing on WanderInk. The team discovered that the Spark device suddenly became unreachable remotely. **Zhang Xiaobai** discovered that Spark had automatically shut down. **Huntun**, with **Qingta's** support, changed the LLM-generated script and storyboard to be generated via Hermes skill calls.

[2026.7.14] **Zhang Xiaobai** assigned tasks to team members. **Bandu Wuzi** and **Huntun** focused on bug fixes and code optimization, while others focused on testing the project code, aiming to release a version by July 18. The text model began using the sponsor-provided Step-3.7-Flash model. Developers submitted code to this repository.

[2026.7.13] **Zhang Xiaobai** found that image editing model calls resulted in black images. After checking by Bandu Wuzi, it was found that sage attention acceleration was added during startup. Changing back to flash-attn acceleration resolved the issue. Zhang Xiaobai repackaged the ComfyUI HTTP service and tested it successfully.

[2026.7.12] The WanderInk team participated in the morning hackathon online training camp and started live text and image broadcasting in the group. **Bandu Wuzi** configured and started the Ollama local model on Spark.

[2026.7.10] **Huntun** submitted the project frontend prototype.

[2026.7.7] **Nancy** submitted the code and documentation for generating scenic stories via LLM. Due to team member **LZH**'s withdrawal, the WanderInk team recruited new member **Huntun** (from Wuxi).

[2026.7.4] **Zhang Xiaobai** installed Hermes on the DGX Spark device: <https://zhuanlan.zhihu.com/p/2056830749530142643>

[2026.7.3] **Zhang Xiaobai** attempted to package **Bandu Wuzi's** ComfyUI service into an HTTP service.

[2026.7.2] The WanderInk team held their second video conference (LZH couldn't attend due to schedule). The project direction of **Audio Comic** was basically confirmed.

[2026.7.1] **Bandu Wuzi** completed the writing of ComfyUI installation, deployment, and usage documentation. Zhang Xiaobai completed the download and deployment attempt of StepFun's Step-3.7-Flash-GGUF model: <https://zhuanlan.zhihu.com/p/2055024035302471223>

[2026.6.30] **Qingta** researched Shanyin's screenwriter master and director master skills, attempted to iterate prompts, installed Claude Code on Spark, and got results. **Nancy** was experimenting with ComfyUI LoRA models. **Bandu Wuzi** verified single-image, dual-image, and triple-image image editing in ComfyUI, successfully generated TTS voice, and suggested **Nancy** find some new model LoRAs.

[2026.6.29] **Zhang Xiaobai** attempted to download and deploy StepFun's Step-3.7-Flash-NVFP4 model. The next day, he announced that both Docker and Conda methods failed to start due to insufficient memory.

[2026.6.28] **Bandu Wuzi** deployed the ComfyUI environment on Spark. He also spent a long time compiling flash-attention from source, generated music using ACE-STEP XL Turbo, and began testing and verifying image generation in ComfyUI. **Qingta** started researching Eazo (<https://creator.eazo.ai/apps>)

[2026.6.27] **Zhang Xiaobai** purchased an intranet penetration cloud service, providing SSH and HTTP channels for team members to share his Spark device. Zhang Xiaobai created this repository.

[2026.6.26] The WanderInk team recruited new member **Qingta** and held their first video conference (Qingta couldn't attend due to schedule) for brainstorming. Team members joined a Feishu enterprise organization and a Feishu group with an OpenClaw bot (**LZH** didn't join due to schedule).

[2026.6.25] The WanderInk team name was confirmed, recruiting new member **Bandu Wuzi** (from Wuxi).

[2026.6.24] WanderInk team members gathered. **Zhang Xiaobai** (Zhang Hui, from Nanjing), **Nancy** (Su Xiaoye, from Chengdu), **LZH** (from Hangzhou) began discussing project direction.

## 📆 Project Team

| Team Member                                  | Responsibility                                                |
| --------------------------------------------| ------------------------------------------------------------ |
| [Zhang Xiaobai](https://github.com/zhanghui-china) | Team Leader, Project Planning, Environment Deployment, Testing, Documentation |
| [Nancy](https://github.com/nancysxy000)     | Team Member, Documentation, Scenic Story Generation, DEMO Video Production |
| [Qingta](https://github.com/DoubleCore)     | Team Member, Skill Development, Hermes Integration, Screenwriter/Director Skill Iteration |
| [Bandu Wuzi](https://github.com/Bandukids)  | Team Member, ComfyUI Service Deployment & Development, Image/Audio Pipeline |
| [Huntun](https://github.com/nativeas)       | Team Member, Web Frontend & Backend Development, Frontend Interaction & Multi-user Design |

![Group Photo](wanderink-group.jpg)

## 💖 Special Thanks

Thanks to NVIDIA for hosting the 2nd DGX Spark Hackathon

![NVIDIA](nvidia-logo.png)

Thanks to Zanqi Technology for competition support

![Zanqi](zanqi-logo.png)

Thanks to StepFun for providing models and online computing resources

![StepFun](stepfun-logo.png)

Thanks to [@Shanyin](https://github.com/Shanyin-ai) for providing professional open-source skill support

![Shanyin](shanyin-logo.png)

## License

This project is licensed under the [Apache License 2.0](https://github.com/comfyanonymous/ComfyUI/blob/master/LICENSE).