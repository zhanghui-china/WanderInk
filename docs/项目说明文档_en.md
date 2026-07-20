# WanderInk (Mànyóu Mòhuì) Project Documentation

> Version: v2.2 (Competition Review Edition)  
> Updated: 2026-07-19  
> Competition: 2nd NVIDIA DGX Spark Hackathon

---

## I. Project Overview

**WanderInk (Chinese name "漫游墨绘" / Mànyóu Mòhuì)** is an end-to-end multimodal creative system for scenic spot cultural IP development. Users only need to input a scenic spot name, and the system automatically completes legend retrieval, script adaptation, storyboard design, character setting, comic page generation, voice dubbing & music composition, and video synthesis, ultimately outputting a **1080P audio comic short video** that is playable and shareable.

Core slogan:

> **Scenic spot name in, audio comic out.**

This project comprehensively upgrades the product philosophy from the 1st Spark Hackathon project [SparkScroll](https://github.com/zhanghui-china/SparkScroll): from "pure comics" to "multimedia storytelling with sound and visuals", from "direct script generation by large models" to "professionalized creation with film industry screenwriter/director skills", from "fully automated black box" to "step-by-step intervenable, re-runnable, reviewable creative workbench", and from "backend gateway focused" to "complete web application supporting multi-user collaboration with friendly interface". In addition to the team leader, this year's team has introduced more professionals with experience in film, AI engineering, and full-stack frontend/backend development.

---

## II. Product Features and Core Highlights

### 2.1 End-to-End Fully Automated Pipeline

The system breaks down scenic spot story creation into **S0–S6 seven sequential steps**, covering five modalities: text, image, speech, music, and video. A single pipeline can go from "scenic spot name" to "MP4 final product" without manual tool switching:

| Step | Name | Output |
|---|---|---|
| S0 | LEGEND Legend Retrieval and Verification | 2–5 candidate legends with source annotations |
| S1 | SCRIPT Script Adaptation | Structured script (acts/scenes/dialogues/voice-overs) |
| S2 | BOARD Storyboard Design | Page-by-page storyboard table (visual descriptions/emotions/text) |
| S3 | ROLE Character Setting | Character cards + front/side/back three-views |
| S4 | PAGES Comic Page Generation | Page-by-page 1920×1080 visuals |
| S5 | VOICE Dubbing & Music | Page-by-page narration audio + BGM |
| S6 | FILM Composite Output | MP4 with subtitles/watermark/end credits |

### 2.2 Character Consistency Without Training

The project's biggest technical highlight is the **"three-view + reference image injection"** character consistency approach:

- S3 generates front/side/back three-views for each main character;
- S4 scales three-views to 768px and passes them as image references in generation requests;
- Combined with fixed art style prefix and character feature prompts, constraining cross-page appearance.

In the M0 consistency checkpoint, the sampling evaluation of White Snake Legend with 2 characters × 3 art styles × 24 images achieved **100% zero identity drift**. Signature props (silver hairpin, paper umbrella, etc.) were preserved throughout, validating the engineering path of "maintaining high consistency without fine-tuning LoRA".

### 2.3 Audio-Visual Synchronization and Robust Audio

- S5 writes back `duration_ms` based on real audio duration, and S6 precisely calculates page length and transition offsets accordingly;
- TTS adopts "whole segment single-shot priority + truncation detection degraded to sentence-by-sentence + three tries take longest" strategy, compatible with both strong and weak models;
- When TTS is completely unavailable, the system generates a silent fallback track based on character count estimation, ensuring complete and intact final product structure.

### 2.4 Step-by-Step Intervenable Creative Workbench

Unlike completely automatic "one-click to end", WanderInk supports step-by-step confirmation by default:

- Each step's output is visualized and previewed on the frontend;
- Users can modify scripts, adjust storyboards, redraw individual pages, re-dub individual audio, and drag to reorder;
- After any step crashes, the breakpoint resume mechanism based on `project.json` only fills missing parts without redoing completed work.

### 2.5 Professional Skill-Driven

The project introduces **Hermes Agent** (configured with glm5.2 model underneath) to host two professional skills: "Screenwriter Master" and "Director Master", covering all text generation in S1–S3:

- **Screenwriter Master Skill**: Responsible for S1 script adaptation, outputting scripts that better conform to film narrative structures (cold opening, exposition-development-climax-resolution, ≤3 main characters);
- **Director Master Skill**: Responsible for S2 storyboard splitting and S3 character feature extraction, outputting storyboard tables with cinematographic language (wide shot/medium shot/close-up, lighting, atmosphere) and emotion tags.

The integration of Hermes injects film industry knowledge into the pipeline, upgrading generated content from "generic LLM text output" to "structured output constrained by professional creative methodologies". WanderInk simply calls the service via OpenAI-compatible protocol with `hermes-agent` as the model name, and the combination of underlying model and skill prompts is managed internally by Hermes.

---

## III. Technical Implementation Plan

### 3.1 Overall Architecture

The project adopts a **"Supervisor + Sequential Pipeline + Pluggable Provider"** architecture:

- **Single `Project` Aggregate Root**: All intermediate states (candidate legends, scripts, storyboards, character cards, page outputs, final outputs) are attached to the same Pydantic `Project` object, serialized to `projects/<id>/project.json`, serving as the single source of truth;
- **OpenAI-Compatible Provider Layer**: All four providers (LLM / Image / TTS / Music) follow OpenAI-compatible protocols, allowing endpoint, model, and key switching via `.env` or Web configuration panel with zero business code changes;
- **CLI / HTTP Dual Entry**: `shanhai` (Typer CLI) and `shanhai-web` (FastAPI) reuse the same `steps/*` and provider layer; the HTTP endpoint places pipelines in background threads, with frontend polling for progress;
- **FFmpeg Functional Synthesis**: `ffmpeg.py` constructs pure commands; S6 concatenates "opening card → page-by-page → closing card → xfade transition → loudness normalization + BGM".

#### Overall Architecture Diagram

![WanderInk Overall Architecture](architecture-hd.png)

> Source file: [architecture.svg](architecture.svg) (vector SVG, 34KB, editable)  
> Rendered output: [architecture.png](architecture.png) (1×, 322KB) · [architecture-hd.png](architecture-hd.png) (2× HD, 1.1MB)  
> Recommended to open SVG files directly in browser for best clarity.

> **Legend Explanation**:
> - Light blue = User layer (Web workbench + CLI + multi-user + AI compliance + Hermes skill)
> - Light yellow = Orchestration layer (S0–S6 seven-step pipeline, Supervisor orchestration)
> - Light gray = Project data layer (aggregate root + breakpoint resume mechanism)
> - Light purple = Provider layer (LLM / Image / TTS / Music + local_backend_guard)
> - Light green = Model service layer (Hermes / ComfyUI / Qwen-TTS / ACE-STEP + tu-zi cloud)
> - Light orange = Output layer (FFmpeg synthesizer → MP4 final product)
> - Light cyan = Deployment environment (DGX Spark local + cloud services)
> - `Project` aggregate root is the single source of truth; all steps read/write the same `project.json`, supporting breakpoint resume.
> - S0–S3 text stages all go through Hermes screenwriter/director skills (underlying glm5.2); images go through tu-zi cloud gpt-image-2.
> - Strictly drawn according to confirmed facts, no unverified data or specific numbers introduced.

#### Model and Endpoint Configuration for Each Pipeline Stage

The table below corresponds one-to-one with the architecture diagram and current `config.json` values, reflecting the "effective model / endpoint" actually called in each stage S0–S5:

| Stage | Purpose | Effective Model | Local Endpoint |
|---|---|---|---|
| S0 Legend | LLM | `Sehyo-Qwen3.5-35B-A3B-NVFP4`, `glm-4.7-flash` (local) / `Step-3.7-Flash` (cloud) | `127.0.0.1:8000` |
| S1 Script | LLM | `hermes-agent` (Screenwriter Master skill, underlying `Step-3.7-Flash`) | `127.0.0.1:8642` |
| S2 Storyboard | LLM | `hermes-agent` (Director Master skill, underlying `Step-3.7-Flash`) | `127.0.0.1:8642` |
| S3 Character Three-views | LLM + Image | LLM: `Sehyo-Qwen3.5-35B-A3B-NVFP4`, `glm-4.7-flash` (local) / `Step-3.7-Flash` (cloud)<br />Image: `gpt-image-2` (cloud) / `Qwen-Image-Edit-2511` (local) | `127.0.0.1:8091` |
| S4 Comic Pages | Image | `gpt-image-2` (cloud) / `Qwen-Image-Edit-2511` (local) | `127.0.0.1:8091` |
| S5 Dubbing/BGM | TTS + Music | `Qwen3-TTS` (local) + `ace-step-v1.5xl` (local) | `127.0.0.1:8090/8092` |

> **Notes**:
>
> - S1–S2 LLM stages all go through local Hermes Agent (`127.0.0.1:8642`), generated by `Step-3.7-Flash` model managed internally by Hermes combined with Screenwriter Master / Director Master skill prompts, constrained by film industry creative methodologies.
> - S3 three-views and S4 page-by-page images go through cloud `gpt-image-2` or local `Qwen-Image-Edit-2511`; three-views passed M0 checkpoint with 100% zero identity drift.
> - S5 dubbing and BGM are all provided locally by DGX (`shanhai-tts` :8090 running `Qwen3-TTS`, `shanhai-music` :8092 running `ACE-STEP 1.5 XL`), protected by `local_backend_guard` global single-concurrency lock.

### 3.2 Multi-Agent Role Design

| Agent | Responsibility | Underlying Capability |
|---|---|---|
| StoryAgent | Scenic spot → Historical story synopsis (S0) | Outline generation |
| ScriptAgent | Story → Script (S1) | Hermes Screenwriter Master skill |
| DirectorAgent | Script → Storyboard (S2) | Hermes Director Master skill |
| CharacterAgent | Script → Character profiles and prop descriptions (S3) | Image editing |
| ImageAgent | Character three-views, storyboard comics (S3/S4) | Image editing |
| VoiceAgent | Storyboard text → Narration voice (S5) | Speech synthesis |
| MusicAgent | Emotion tags → Background music (S5) | Music generation |
| ComposerAgent | Visuals + Voice + Music → MP4 (S6) | FFmpeg + PIL layout |

### 3.3 Key Technical Details

- **Subtitle and Visual Layering**: `compose_page()` only outputs 1920×1080 full-frame base image; `overlay_layer()` separately generates transparent PNG for subtitles and "AI Generated" watermark; ffmpeg overlay is applied after Ken Burns scaling to avoid text shaking with camera movement or watermark being cropped out of frame.
- **Atomic Write and Reentrant**: `store.save()` uses "write to `.tmp` then `os.replace`" atomic write, persisting at each step; S3 `locked` is idempotent, S4/S5 output existence validation supports breakpoint resume after any step crash.
- **Local Backend Global Single Concurrency**: `providers/_http.py` automatically identifies `127.0.0.1`/`localhost` endpoints through `local_backend_guard`, globally queuing Ollama/vllm/ComfyUI/Qwen-TTS/ACE-Step sharing GPU on DGX Spark, avoiding timeout caused by multi-task GPU contention.
- **Stage-Specific Configuration Override**: Web configuration panel supports "global default + S0–S5 stage-specific override", e.g., S1–S2 all use Hermes screenwriter/director skills (underlying glm5.2), images use cloud or local ComfyUI, taking effect without restart.

---

## IV. Architecture Design Philosophy and Optimization Plan

### 4.1 DGX Spark Platform Adaptation Philosophy

The core constraint of DGX Spark (GB10, 128GB unified memory) is "multiple models cannot reside simultaneously". Therefore, this project does not attempt to load all models at once, but adopts a strategy of **time-slice loading + local service shim + global single-concurrency lock**:

1. **Text Phase**: VLLM local LLM (`Sehyo-Qwen3.5-35B-A3B-NVFP4`) permanently resides at ~35GB;
2. **Image Phase**: Bridge to ComfyUI (`Qwen-Image-Edit-2511`) via `shanhai-image.service`;
3. **Audio Phase**: `shanhai-tts.service` (`Qwen3-TTS`) and `shanhai-music.service` (`ACE-STEP 1.5 XL`) time-share GPU;
4. **Composition Phase**: ComposerAgent consumes only ~2GB, using ffmpeg for final output.

This design fully leverages DGX Spark's platform advantages of **large unified memory, single-machine multimodal collaboration, and local closed-loop inference**.

### 4.2 Implemented Key Optimizations

| Optimization | Problem | Solution | Effect |
|---|---|---|---|
| Character Consistency | Cross-page character appearance drift | Three-view reference images + 768px scaled upload + fixed art style prefix | 100% pass M0 checkpoint |
| Vertical Image Filling | gpt-image-2 produces vertical images causing black bars | Complete frame centered + same-image blurred darkening to fill sides | Professional final product appearance |
| S4 Concurrency | Sequential page generation too slow | ThreadPool max 3, local GPU automatically serializes | ~23% speedup in cloud scenarios |
| Silent Fallback | TTS unavailability causes incomplete final product | Generate silent track based on character count estimation | End-to-end output even if TTS completely fails |
| Network Fault Tolerance | Proxy transient 503/RemoteProtocolError | Capture complete `httpx.TransportError` family with exponential backoff | Improved stability in real DGX deployment |
| Configuration Panel | Endpoint changes require file modification and restart | Web UI runtime override, persisted to `config.json` | Model switching without restart |
| GPU TTS | DGX has no online TTS | Deploy Qwen-TTS shim, PyTorch nightly adapts to GB10 sm_121 | Local human voice now working |
| AI BGM | Empty music library | ACE-STEP 1.5 XL generates pure instrumental BGM locally | S5 can now output real background music |

### 4.3 Optimization Plan

The following features have been implemented:

- **Multi-user login and queue visibility**: Cookie session + `users.json`, supports team sharing, only see own cancellation permissions;
- **National 5A scenic spot information entry**: Extracted 359 5A scenic spots from cultural and tourism bureau official website, allowing users to quickly input;

The following features are planned:

- **Multi-panel comic layout per page**: Japanese-style multi-panel storyboards for stronger pacing and visual hierarchy;
- **Character library cross-project reuse**: Store locked characters in global library, reduce redundant generation;
- **PDF refined layout and multi-language output**: Targeting B-end scenic spot operators;
- **Cost cap and redraw budget control**: Prevent remote API quota runaway;
- **Custom three-view support**: Allow users to design their own IP characters;
- **Real scenic spot image composition**: Allow users to upload real scenic spot images for comic and video composition.

---

## V. AI Compliance Handling and Sensitive Information Management

This project builds AI content compliance as a **non-disablable hard rule** into the synthesis process:

1. **"AI Generated" watermark per page**: Transparent overlay layer displays stroked watermark at fixed upper-right corner, not affected by Ken Burns camera movement;
2. **AI generation identification**: Both images and videos clearly marked with "WanderInk AI-assisted generation";
3. **Precise source annotation**: Distinguish by `source_type` between "official history / local gazette / folk legend / literary work / original interpretation", original interpretations do not impersonate legend sources;
4. **Sensitive content filtering**: S0 sends candidate legends involving religion, ethnicity, and modern political figures to sensitive review list; user manual clearly indicates such scenic spots can take "custom story" path;
5. **Child-friendly content protection**: Automatically avoid violence/horror details for child audiences;
6. **Copyright boundary**: Users uploading custom stories must self-certify rights; system outputs clearly mark source and AI generation identity.

Future planning: Introduce input/output bilateral content safety filtering API for automatic review of text-to-image prompts and generated results.

---

## VI. Deployment Plan

### 6.1 Install ComfyUI Environment

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

# Install PyTorch according to GPU CUDA version
pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130

# Install ComfyUI dependencies
cd ~/ComfyUI
pip install -r requirements.txt
```

Edit `~/.config/systemd/user/comfyui.service` to configure systemctl service:

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

### 6.2 Install Web Service Environment

```bash
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

Edit `~/.config/systemd/user/shanhai-web.service` to configure systemctl service:

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

Edit `~/.config/systemd/user/shanhai-image.service` to configure systemctl service:

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

Edit `~/.config/systemd/user/shanhai-tts.service` to configure systemctl service:

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

Edit `~/.config/systemd/user/shanhai-music.service` to configure systemctl service:

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

### 6.3 Install Hermes Environment

Refer to <https://zhuanlan.zhihu.com/p/2056830749530142643>

For security reasons, the hermes environment is isolated from the wanderink runtime environment to prevent hermes from deleting project code and documents after gaining high permissions.

```bash
# Login as hermes user
source ~/.bashrc

# Install dependencies
sudo apt install ripgrep

# Install hermes
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash

# Configure hermes
hermes setup
```

### 6.4 Model Downloads

#### 6.4.1 Download LLM Model: Sehyo-Qwen3.5-35B-A3B-NVFP4

Edit file `download_model_by_modelscope_Sehyo-Qwen3.5-35B-A3B-NVFP4.py`:

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

print(f"Download completed, files saved in: {model_dir}")
```

Download the model:

```bash
python download_model_by_modelscope_Sehyo-Qwen3.5-35B-A3B-NVFP4.py
```

#### 6.4.2 Download ComfyUI Image Editing Model: Qwen-Image-Edit-2511

> [!NOTE]
> The system's `~/ComfyUI/models` directory is a symbolic link pointing to the actual storage directory `~/models/comfyui_models`. The following operations will directly download model files to the actual directory.

Download `qwen_image_edit_2511_fp8mixed.safetensors` and save it to `~/models/comfyui_models/diffusion_models/` directory.

You can download using one of the following methods:

- **Using wget (Recommended, with domestic mirror acceleration)**:

  ```bash
  export HF_ENDPOINT=https://hf-mirror.com # Domestic mirror source acceleration
  huggingface-cli download Comfy-Org/Qwen-Image-Edit_ComfyUI split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors --local-dir ~/models/comfyui_models/diffusion_models --local-dir-use-symlinks False
  
  # After download, move the file to the correct root directory and clean up empty directories
  mv ~/models/comfyui_models/diffusion_models/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors ~/models/comfyui_models/diffusion_models/
  rm -rf ~/models/comfyui_models/diffusion_models/split_files
  ```

- **Using huggingface-cli**:

  ```bash
  # Create directory (if not exists)
  mkdir -p ~/models/comfyui_models/diffusion_models
  
  # Download model file
  wget -O ~/models/comfyui_models/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors \
    https://hf-mirror.com/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_2511_fp8mixed.safetensors
  ```

#### 6.4.3 Download ComfyUI Speech Synthesis Model: Qwen3-TTS

Download the complete `Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign` repository and save it to `~/models/comfyui_models/qwen-tts/Qwen3-TTS-12Hz-1.7B-VoiceDesign` directory.

You can download using one of the following methods:

- **Using huggingface-cli (Recommended, with domestic mirror acceleration)**:

  ```bash
  # Create directory (if not exists)
  mkdir -p ~/models/comfyui_models/qwen-tts/Qwen3-TTS-12Hz-1.7B-VoiceDesign
  
  # Download complete model repository
  export HF_ENDPOINT=https://hf-mirror.com # Domestic mirror source acceleration
  huggingface-cli download Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign \
    --local-dir ~/models/comfyui_models/qwen-tts/Qwen3-TTS-12Hz-1.7B-VoiceDesign \
    --local-dir-use-symlinks False
  ```

* **Using git clone (requires git-lfs installed)**:

  ```bash
  # Clone repository to specified location
  git clone https://hf-mirror.com/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign ~/models/comfyui_models/qwen-tts/Qwen3-TTS-12Hz-1.7B-VoiceDesign
  ```

* **Official Hugging Face Link**:
  [Qwen3-TTS-12Hz-1.7B-VoiceDesign](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign/tree/main)

#### 6.4.4 Download ComfyUI Music Generation Model: ACE-STEP XL Turbo

Download the music generation model `acestep1.5XL_ComfyUI_aio-marduk191.safetensors` and save it to `~/models/comfyui_models/checkpoints/` directory.

You can download using one of the following methods:

- **Using wget (Recommended, with domestic mirror acceleration)**:

  ```bash
  export HF_ENDPOINT=https://hf-mirror.com # Domestic mirror source acceleration
  huggingface-cli download marduk191/acestep1.5XL_ComfyUI_aio-marduk191 acestep1.5XL_ComfyUI_aio-marduk191.safetensors --local-dir ~/models/comfyui_models/checkpoints --local-dir-use-symlinks False
  ```

- **Using huggingface-cli**:

  ```bash
  # Create directory (if not exists)
  mkdir -p ~/models/comfyui_models/vae
  
  # Download model file
  wget -O ~/models/comfyui_models/vae/qwen_image_vae.safetensors \
    https://hf-mirror.com/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors
  ```

- **Official Hugging Face Link**:
  [acestep1.5XL_ComfyUI_aio-marduk191.safetensors](https://huggingface.co/marduk191/acestep1.5XL_ComfyUI_aio-marduk191/tree/main)

#### 6.4.5 Download ComfyUI VAE Model

Download `qwen_image_vae.safetensors` and save it to `~/models/comfyui_models/vae/` directory.

You can download using one of the following methods:

- **Using wget (Recommended, with domestic mirror acceleration)**:

  ```bash
  export HF_ENDPOINT=https://hf-mirror.com # Domestic mirror source acceleration
  huggingface-cli download marduk191/acestep1.5XL_ComfyUI_aio-marduk191 acestep1.5XL_ComfyUI_aio-marduk191.safetensors --local-dir ~/models/comfyui_models/checkpoints --local-dir-use-symlinks False
  ```

- **Official Hugging Face Link**:
  [qwen_image_vae.safetensors](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/blob/main/split_files/vae/qwen_image_vae.safetensors)

#### 6.4.6 Download ComfyUI CLIP/Text Encoder Models

Download `qwen_2.5_vl_7b.safetensors` and `qwen3vl_4b_bf16.safetensors` and save them to `~/models/comfyui_models/text_encoders/` directory.

You can download using one of the following methods:

- **Using wget (Recommended, with domestic mirror acceleration)**:

```bash
# Create directory (if not exists)
mkdir -p ~/models/comfyui_models/text_encoders

# Download qwen_2.5_vl_7b
wget -O ~/models/comfyui_models/text_encoders/qwen_2.5_vl_7b.safetensors \
  https://hf-mirror.com/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b.safetensors

# Download qwen3vl_4b_bf16
wget -O ~/models/comfyui_models/text_encoders/qwen3vl_4b_bf16.safetensors \
  https://hf-mirror.com/Comfy-Org/Krea-2/resolve/main/text_encoders/qwen3vl_4b_bf16.safetensors
```

- **Using huggingface-cli**:

  ```bash
  # Create directory (if not exists)
  mkdir -p ~/models/comfyui_models/checkpoints
  
  # Download model file
  wget -O ~/models/comfyui_models/checkpoints/acestep1.5XL_ComfyUI_aio-marduk191.safetensors \
    https://hf-mirror.com/marduk191/acestep1.5XL_ComfyUI_aio-marduk191/resolve/main/acestep1.5XL_ComfyUI_aio-marduk191.safetensors
  ```

- **Official Hugging Face Links**:

  - [qwen_2.5_vl_7b.safetensors](https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/blob/main/split_files/text_encoders/qwen_2.5_vl_7b.safetensors)
  - [qwen3vl_4b_bf16.safetensors](https://huggingface.co/Comfy-Org/Krea-2/blob/main/text_encoders/qwen3vl_4b_bf16.safetensors)

#### 6.4.7 Download ComfyUI LoRA Models

##### 6.4.7.1 Qwen-Image-Edit-2511

Download `Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors` and save it to `~/models/comfyui_models/loras/` directory.

You can download using one of the following methods:

- **Using wget (Recommended, with domestic mirror acceleration)**:

  ```bash
  # Create directory (if not exists)
  mkdir -p ~/models/comfyui_models/loras
  
  # Download LoRA model file
  wget -O ~/models/comfyui_models/loras/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors \
    https://hf-mirror.com/lightx2v/Qwen-Image-Edit-2511-Lightning/resolve/main/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors
  ```

- **Official Hugging Face Link**:
  [Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning/blob/main/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors)

##### 6.4.7.2 Other LoRA Models

Other LoRA models can be downloaded from civital.com and saved to `~/models/comfyui_models/loras/` directory after download.

- **LoRA Model Links**:
  - [Real_ani_qwen](https://civitai.com/models/2164588/gen-ani-art-style-qwen-lora)
  - [Qwen-image_2511_Edit_Ball-jointed_Doll V2.0](https://civitai.com/models/2303022/qwen-image2511editball-jointeddoll-v20)
  - [Nano banana figurine style](https://civitai.com/models/1900696/nano-banana-figurine-style-qwen-image-edit)

### 6.5 Download vllm Docker Image

```bash
docker pull vllm/vllm-openai:cu130-nightly
```

### 6.6 System Startup

#### 6.6.1 Start ComfyUI Service

```bash
# Login as wuzi user
source ~/.bashrc

# Start ComfyUI service
systemctl --user restart comfyui
systemctl --user status comfyui
```

The service will start on port `8188`.

#### 6.6.2 Start Web Services

```bash
# Login as huntun user
source ~/.bashrc

# Start Web, TTS, image, and music services
systemctl --user start shanhai-web shanhai-tts shanhai-image shanhai-music

# Check service status
systemctl --user status shanhai-web shanhai-tts shanhai-image shanhai-music
```

The services will start on ports `8090, 8091, 8092`.

#### 6.6.3 Start Hermes Service

```bash
# Login as hermes user
source ~/.bashrc

hermes gateway restart
```

#### 6.3.4 LLM Model Startup

##### 6.3.4.1 vllm docker startup for Qwen3.5-35B-A3B-NVFP4 model

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

Start Sehyo-Qwen3.5-35B-A3B-NVFP4 using docker:

```bash
cd /home1/wuzi/docker
chmod +x docker_start_Sehyo-Qwen3.5-35B-A3B-NVFP4.sh
sh ./docker_start_Sehyo-Qwen3.5-35B-A3B-NVFP4.sh
docker ps
docker logs [containerid]
```

##### 6.3.4.2 ollama startup for glm-4.7-flash model

Edit `~/.config/systemd/user/ollama-preload.service` to configure systemctl service:

```
[Unit]
Description=Ollama GLM Model Preloader
After=network.target

[Service]
Type=oneshot
ExecStartPre=/usr/bin/sleep 10
ExecStart=/usr/bin/curl --noproxy "*" -X POST http://127.0.0.1:11434/api/generate -d '{"model": "glm-4.7-flash:latest", "keep_alive": -1}'
RemainAfterExit=yes

[Install]
WantedBy=default.target
```

Start Ollama service:

```bash
# Login as wuzi user
source ~/.bashrc

# Start Ollama preload service
systemctl --user restart ollama-preload
systemctl --user status ollama-preload
```

The service will start on port `11434`.

---

## VII. Platform Adaptability: NVIDIA DGX Spark + Open Source Models + StepFun Models

| Layer | Technology/Model | Purpose |
|---|---|---|
| Hardware Platform | NVIDIA DGX Spark (GB10, 128GB Unified Memory) | Single machine hosting LLM + Image + TTS + Music full pipeline inference |
| LLM | Ollama (`glm-4.7-flash`), vllm (`Sehyo-Qwen3.5-35B-A3B-NVFP4`), Stepfun (`step-3.7-flash`) | Story/Script/Storyboard/Character description |
| Image | ComfyUI + `Qwen-Image-Edit-2511`, `gpt-image-2` | Three-views, storyboard comics, image editing |
| Speech | Qwen3-TTS | Narration dubbing |
| Music | ACE-STEP 1.5 XL | Background music generation |
| Orchestration | FastAPI + ThreadPool + FFmpeg | Pipeline scheduling and video composition |

The `step-3.7-flash` model provided by StepFun serves as one of the main text generation paths, significantly improving the reasoning quality of scripts and storyboards; NVIDIA DGX Spark's unified memory and local GPU capabilities make "single-machine closed-loop without external cloud services" possible.

### 7.1 Sehyo-Qwen3.5-35B-A3B-NVFP4 Model

This project selects **Sehyo/Qwen3.5-35B-A3B-NVFP4** as one of the core text models deployed locally on DGX Spark. The model is quantized based on Qwen3.5-35B-A3B MoE architecture, maintaining the original model's capabilities while completely preserving **MTP (Multi-Token Prediction)** weights. It can further combine vLLM's **Speculative Decoding** technology to improve inference throughput, making it a cost-effective solution in the Qwen3.5 series that balances performance, cost, and deployment efficiency.

The model adopts **NVFP4 (NVIDIA Floating Point 4-bit)** quantization format natively supported by the NVIDIA Blackwell platform. NVFP4 uses a hybrid design of **FP4 weights + FP8 Scale**, significantly reducing model storage and computation overhead while preserving model accuracy as much as possible. Compared to the original BF16 model, NVFP4 achieves approximately **4x model compression ratio**, significantly reducing memory footprint and memory bandwidth pressure, thereby improving GPU data reading efficiency.

Compared with traditional INT4 quantization schemes, NVFP4 uses floating-point representation, providing better expressive ability for weights in different numerical ranges, effectively reducing quantization errors and maintaining model inference accuracy. For MoE (Mixture of Experts) models like Qwen3.5-35B-A3B, NVFP4 can better preserve the capability characteristics of each Expert, reducing the risk of performance degradation after quantization.

Relying on NVIDIA Blackwell architecture's native support for NVFP4, the model inference process can directly utilize Tensor Core for hardware acceleration, achieving higher Token generation speed and Batch throughput while ensuring generation quality. For scenarios in this project such as large model Agent, multi-round dialogue, code generation, and data analysis, it can achieve better deployment effect and operational efficiency under limited computing resources.

**Overall, Sehyo/Qwen3.5-35B-A3B-NVFP4 achieves a good balance between model capability, inference performance, memory footprint, and deployment cost, making it one of the important foundation models for DGX Spark local large model inference platform.**

### 7.2 StepFun step-3.7-flash Model

This project integrates the **StepFun Step-3.7-Flash** large language model as one of the core text models for cloud deployment. Step-3.7-Flash is a new-generation high-performance inference model launched by StepFun. While maintaining strong comprehensive capabilities, it has been deeply optimized for inference speed, response latency, and deployment cost, providing stable and efficient model services for scenarios such as AI Agent, multi-round dialogue, code generation, knowledge Q&A, and content creation.

Compared with traditional large-parameter models, Step-3.7-Flash emphasizes **the balance between inference efficiency and actual application experience**. The model has fast first Token response speed (TTFT) and high Token output throughput, maintaining smooth interaction experience in complex task processing, especially suitable for application development needs in hackathon scenarios that require rapid iteration, real-time verification, and high-concurrency calls.

In this project, Step-3.7-Flash mainly undertakes core responsibilities such as hermes base model, scenic spot story search and source verification, and character feature generation. Benefiting from its excellent Chinese understanding ability and tool calling ability, the model can effectively support task decomposition, context reasoning, and result generation in multi-Agent workflows, providing stable and reliable intelligent core capability for the entire system.

At the same time, as an important model and computing power supporter of this AI hackathon, StepFun provides participating teams with high-performance model services and computing resource guarantees, enabling developers to focus more on product innovation and scenario implementation without paying too much attention to underlying infrastructure construction. With the capability support of Step-3.7-Flash, this project quickly completed prototype construction, functional verification, and effect optimization during the development cycle, significantly improving R&D efficiency and project delivery quality.

**Overall, Step-3.7-Flash achieves a good balance between model capability, inference efficiency, response speed, and engineering implementability, providing stable, efficient, and easy-to-integrate large model capability support for this project, making it an important infrastructure for the project's intelligent capabilities.**

### 7.3 ACE Studio & StepFun (StepFun) ACE-Step 1.5 XL Model

This project selects **ACE-Step 1.5 XL**, jointly launched by ACE Studio and StepFun, as the core music generation model in the audio generation stage, providing high-quality soundtrack generation capability for the system. ACE-Step 1.5 XL integrates ACE Studio's technical accumulation in professional AI music generation and StepFun's capabilities in large model training and inference, automatically completing lyrics understanding, melody generation, arrangement creation, and vocal synthesis based on natural language descriptions, achieving end-to-end generation from creativity to complete music works.

Compared with traditional music generation solutions, ACE-Step 1.5 XL has significantly enhanced music structure consistency, vocal naturalness, and style control capability. The model can better understand user input themes, emotions, styles, and scene requirements, generating music works with high audibility and completeness while ensuring melody fluency. Whether it's pop, electronic, rock, Chinese style, or film soundtracks, it can achieve good style transfer and content creation effects.

In this project, ACE-Step 1.5 XL mainly undertakes tasks such as scene background music generation and audio material rapid production. Through natural language-driven music generation, the project can quickly obtain original music content that meets plot and scene requirements without professional music production team participation, significantly reducing content production threshold and improving creation efficiency. The model supports long-duration music content generation, meeting the needs of short dramas, comic videos, promotional videos, and digital content creation.

As one of the important generative AI capabilities in the StepFun ecosystem, ACE-Step 1.5 XL completes the complete AIGC content production pipeline from text generation, image generation to music generation for this project. Through collaborative work with large language models, multi-Agent systems, and visual generation models, it achieves a fully intelligent content production closed loop of "story generation — character shaping — visual creation — music production".

**Overall, ACE-Step 1.5 XL achieves a good balance between music quality, generation efficiency, and creative freedom, providing professional-level AI music creation capability for this project, making it an important part of building a multimodal content generation system.**

---

## VIII. Project Completeness

- **Functionally Complete**: All S0–S6 seven steps are fully implemented; frontend supports creating, previewing, editing, redrawing, re-dubbing, downloading MP4 video/PDF/image package;
- **Frontend-Backend Complete**: Backend FastAPI + frontend React + Vite + Tailwind, supporting multi-user, queue, and share links;
- **Stable Operation**: 300+ unit test cases covering core paths; real end-to-end has produced final products for Leifeng Pagoda, Yellow Crane Tower, etc.;
- **Documented**: Including PRD, product plan, deployment manual, user manual, decision records (decisions 0001–0006), full repository research report;
- **Demo-ready**: After inputting scenic spot name, frontend displays real-time progress and time consumption for each step, with final playable MP4.

---

## IX. Demo Effect

Demo planned to be presented according to the following script:

| Scene | Duration | Content | Key Points to Demonstrate |
|---|---|---|---|
| Scene 1 | 30s | Problem introduction: long cycle and high cost of scenic spot IP creation | Industry pain point data |
| Scene 2 | 30s | Input "Leifeng Pagoda/West Lake" → S0/S1 automatically generates legend and script | Web real-time progress |
| Scene 3 | 60s | Character three-views + page-by-page comic generation | Three-views and comic pages |
| Scene 4 | 45s | Dubbing, music, and final composition | Play voice and BGM clips |
| Scene 5 | 15s | Final product playback + data: 1 scenic spot name → 1 audio comic | Complete MP4 playback |

---

## X. Competition Essay: DGX Spark Hackathon "Ten-Day Journey" Development Process

WanderInk's creation spans the complete cycle of the 2nd DGX Spark Hackathon, which the team calls "Ten-Day Journey":

- **D1–D2 (6.24–6.25)**: Team name determined, members assembled, brainstorming to confirm "audio comic" direction;
- **D3–D4 (6.28–6.29)**: Bandukids (般度五子) deployed ComfyUI on DGX Spark, compiled flash-attention from source, verified ACE-STEP music generation; Zhang Xiaobai (张小白) attempted StepFun model deployment;
- **D5–D6 (6.30–7.1)**: Qingta (轻踏) researched Shanyin screenwriter/director skills, Nancy experimented with ComfyUI LoRA, team installed Claude Code;
- **D7–D8 (7.7–7.10)**: Nancy submitted scenic spot story generation code, Huntun (馄饨) joined and submitted frontend prototype;
- **D9–D10 (7.11–7.15)**: DGX deployment systemd service化, GPU TTS, local AI BGM, Web configuration panel, Hermes screenwriter skill launched one after another; frontend completed "Tianqing Yanyu" visual redesign and multi-user design.

Key issues and fixes are documented in `web/docs/decisions/`, demonstrating the team's engineering iteration ability under real hardware constraints.

---

## XI. Differences from SparkScroll (1st Spark Hackathon)

This project has undergone professional upgrade based on the philosophy of [SparkScroll](https://github.com/zhanghui-china/SparkScroll):

| Dimension | 1st SparkScroll | This Year's WanderInk |
|---|---|---|
| Output Form | Pure comic images | Audio comic short video (visuals + narration + BGM) |
| Script Source | Large model + prompt direct generation | Introduced film industry "Screenwriter Master / Director Master" skills |
| Creative Process | Fully automated black box | Step-by-step intervenable, redrawable, re-dubbable, reviewable |
| Frontend Role | Backend gateway focused | Frontend as important display entry, supporting multi-user collaboration |
| Character Consistency | Not systematically solved | Three-view reference images + fixed art style + consistency checkpoint |
| Platform Adaptation | Proof of concept | DGX Spark local LLM + Image + TTS + Music full-stack closed loop |
| Team | Members changed except leader | Introduced professionals in film skills, frontend/backend, ComfyUI engineering |

In one sentence: **The team leader remains the same, but the product form, technical depth, and team configuration have all undergone qualitative changes.**

---

## XII. Evaluation Criteria Comparison Table

| Evaluation Dimension | Weight | Corresponding Content in This Project |
|---|---|---|
| Practicality, Industry Implementation Value & Technical Innovation | 25% | Solves pain points of中小景区 IP development "long cycle, high cost"; end-to-end audio comic solution has industry pioneering nature; fully utilizes DGX Spark unified memory to achieve single-machine closed loop |
| Agent Integration & Model Optimization Technical Depth | 25% | Multi-Agent collaboration (Story/Script/Director/Character/Image/Voice/Music/Composer); Hermes "Screenwriter Master/Director Master" skill injection; three-view consistency approach; TTS truncation detection and silent fallback |
| Project Completeness | 20% | S0–S6 functionally complete; FastAPI + React frontend-backend complete; 300+ test cases; PRD/deployment manual/user manual/decision records complete; live demo ready |
| Platform Adaptability | 15% | DGX Spark 128GB unified memory time-slice loading; vllm local LLM; ComfyUI image pipeline; Qwen3-TTS speech; ACE-STEP music; Stepfun step-3.7-flash text model |
| Demo Effect | 10% | Web real-time progress + three-views/page-by-page preview + final MP4 playback; Demo script 3-minute complete closed loop |
| Competition Essay | 5% | "Ten-Day Journey" development process, decision records, issue fixes fully documented in repository `web/docs/decisions/` and README update notes |

---

## XIII. Project Team

| Member | Responsibility |
|---|---|
| [Zhang Xiaobai](https://github.com/zhanghui-china) | Team leader, project planning, environment deployment, testing, documentation |
| [Nancy](https://github.com/nancysxy000)     | Team member, documentation, scenic story generation, demo video production |
| [Qingta](https://github.com/DoubleCore)       | Team member, Skill development, Hermes integration, screenwriter/director skill iteration |
| [Bandukids](https://github.com/Bandukids)    | Team member, ComfyUI service deployment & development, image/audio pipeline |
| [Huntun](https://github.com/nativeas)         | Team member, Web frontend & backend development, frontend interaction & multi-user design |

---

## XIV. Team and Project Updates

[2026.7.19] **Zhang Xiaobai** (张小白) and **Bandukids** (般度五子) improved deployment documentation and project introduction documents.

[2026.7.18] Project team members conducted a series of tests and verifications. **Huntun** (馄饨) fixed Web bugs, and the Web code was finalized.

\[2026.7.15] Project team members conducted intensive testing on WanderInk. During testing, the Spark device suddenly became unreachable remotely. **Zhang Xiaobai** (张小白) discovered that Spark had automatically shut down. With support from **Qingta** (轻踏), **Huntun** (馄饨) changed the LLM script and storyboard generation to use Hermes skill calls.

\[2026.7.14] **Zhang Xiaobai** (张小白) assigned tasks to team members. **Bandukids** (般度五子) and **Huntun** (馄饨) focused on BugFix and code optimization, while others focused on testing project code, aiming to release a version by July 18. Text models started using the sponsor-provided step-3.7-flash model. Developers submitted code to this repository.

\[2026.7.13] **Zhang Xiaobai** (张小白) discovered that image editing model calls produced black results. After **Bandukids** (般度五子) investigated, it was found that the issue was caused by enabling sage attention during startup; switching back to flash-attn solved the problem. Zhang Xiaobai repackaged the ComfyUI HTTP service and passed testing.

\[2026.7.12] The WanderInk team participated in the morning hackathon online training camp and started live updates in the group. **Bandukids** (般度五子) configured and started Ollama local models on Spark.

\[2026.7.10] **Huntun** (馄饨) submitted the project frontend prototype.

\[2026.7.7] **Nancy** submitted code and documentation for generating scenic spot stories via LLM. Due to team member **LZH** leaving the project, the WanderInk team recruited new member **Huntun** (馄饨) from Wuxi.

\[2026.7.4] **Zhang Xiaobai** (张小白) installed Hermes on the DGX Spark device. See: <https://zhuanlan.zhihu.com/p/2056830749530142643>

\[2026.7.3] **Zhang Xiaobai** (张小白) attempted to package **Bandukids**' (般度五子) ComfyUI service as an HTTP service.

\[2026.7.2] The WanderInk team held their second video conference (LZH was unable to attend). They basically confirmed the **audio comic** project direction.

\[2026.7.1] **Bandukids** (般度五子) completed writing ComfyUI installation, deployment, and usage documentation. Zhang Xiaobai completed downloading and deployment attempts for the Stepfun Step-3.7-Flash-GGUF model. See: <https://zhuanlan.zhihu.com/p/2055024035302471223>

\[2026.6.30] **Qingta** (轻踏) researched Shanyin's screenwriter master and director master skills, attempted prompt iteration, installed Claude Code on Spark, and obtained results. **Nancy** was experimenting with ComfyUI Lora models. **Bandukids** (般度五子) conducted ComfyUI environment verification for single-image, double-image, and triple-image editing, got TTS voice generation working, and suggested **Nancy** find new model Loras.

\[2026.6.29] **Zhang Xiaobai** (张小白) attempted to download and deploy the Stepfun Step-3.7-Flash-NVFP4 model. The next day, it was announced that both docker and conda methods failed due to insufficient memory.

\[2026.6.28] **Bandukids** (般度五子) deployed the ComfyUI environment on Spark. He also spent a long time compiling flash-attention from source, generated music using ACE-STEP XL Turbo, and started testing and verifying image generation with ComfyUI. **Qingta** (轻踏) began researching Eazo (<https://creator.eazo.ai/apps>)

\[2026.6.27] **Zhang Xiaobai** (张小白) purchased an intranet penetration cloud service, providing SSH and HTTP channels for team members to share his Spark device. Zhang Xiaobai created this repository.

\[2026.6.26] The WanderInk team recruited new member **Qingta** (轻踏) and held their first video conference (Qingta was unable to attend). Team members joined a Feishu (Chinese enterprise collaboration platform) organization and a Feishu group with OpenClaw bot (**LZH** did not join).

\[2026.6.25] The WanderInk team name was confirmed. New member **Bandukids** (般度五子) from Wuxi was recruited.

\[2026.6.24] WanderInk team members were assembled. **Zhang Xiaobai** (张小白/Zhang Hui from Nanjing), **Nancy** (粟小叶 from Chengdu), and **LZH** (from Hangzhou) began discussing project directions.

---

## XV. Conclusion

WanderInk is not just an "AI-generated video" toy, but a **professional, intervenable, deployable** multimodal creative system for scenic spot cultural IP production. It fully leverages NVIDIA DGX Spark's unified memory advantages, integrating Stepfun large models, Hermes professional skills, ComfyUI image pipeline, and FFmpeg composition capabilities, representing a complete practice of "AI creative industrialization".

> Scenic spot name in, audio comic out. WanderInk, let every scenery have a story to tell.