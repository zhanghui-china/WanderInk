# ComfyUI 安装与部署手册

由于核心的 ComfyUI 引擎、第三方自定义节点（Custom Nodes）以及 G 级模型文件（Checkpoints/LoRAs）体积极大，本仓库（`WanderInk`）**未将其提交至 GitHub**。

为了使其他部署者能够在新环境中复现并运行这套多模态（文生图、图像融合、TTS语音合成、AI歌曲生成）服务，请按照本手册指引完成 ComfyUI 及其环境的安装配置。

---

## 一、 基础环境准备

### 1. 克隆 ComfyUI 官方仓库
在主目录下克隆标准的 ComfyUI 仓库：
```bash
cd ~
git clone https://github.com/comfyanonymous/ComfyUI.git
```

### 2. 创建 Conda 虚拟环境
推荐使用 Anaconda / Miniconda 管理依赖，Python 版本建议为 `3.11` 或 `3.12`：
```bash
# 创建环境
conda create -n comfyui python=3.12 -y
# 激活环境
conda activate comfyui
```

### 3. 安装 PyTorch (支持 CUDA 加速)
根据你的显卡 CUDA 版本安装对应的 PyTorch（建议 CUDA 12.1+）：
```bash
# CUDA 12.1 安装命令
pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
```

### 4. 安装 ComfyUI 基础依赖
进入 ComfyUI 根目录并安装其常规 requirements 依赖：
```bash
cd ~/ComfyUI
pip install -r requirements.txt
```

---

## 二、 关键自定义节点（Custom Nodes）安装

多模态工作流（图片编辑、语音合成、音乐生成）依赖于若干特殊的自定义节点。

### 1. 安装插件管理器 ComfyUI-Manager (必备)
```bash
cd ~/ComfyUI/custom_nodes
git clone https://github.com/ltdrdata/ComfyUI-Manager.git
```

### 2. 本项目依赖的自定义插件 (参考)
在启动 ComfyUI 界面后，建议在 **Manager -> Install Missing Custom Nodes** 中自动扫描并安装工作流所需的全部节点；或者手动克隆以下节点至 `custom_nodes/` 目录：
- **ComfyUI-AudioNodes** / **ComfyUI-Foley**（音频与音乐生成组件）
- **ComfyUI-Qwen-Nodes**（支持 Qwen 系列多模态大模型的推理节点）
- **ComfyUI-Flux-Accelerate**（Flux 图像生成加速推理节点）

---

## 三、 模型文件部署规范 (Models Deployment)

所有下载的模型权重文件，必须准确存放在 `~/ComfyUI/models/` 对应子目录中（若运行出错，请检查模型名称和存放路径）：

| 模型类型 | 推荐模型名称 | 存放路径 (相对于 `ComfyUI/`) |
| :--- | :--- | :--- |
| **大底模 (Checkpoints)** | `Flux-dev.safetensors` 或 `ACE-STEP-XL-Turbo.safetensors` | `models/checkpoints/` |
| **图像文本编码器 (Clip)** | `t5xxl_fp16.safetensors` / `clip_l.safetensors` | `models/clip/` |
| **VAE 编解码器 (VAE)** | `ae.safetensors` | `models/vae/` |
| **TTS 语音声学模型** | Qwen 语音模型权重文件 | `models/audio/` 或对应自定义节点专属路径 |

*注意：具体模型版本请参考 [comfyui-bridge/](file:///home1/wuzi/WanderInk/comfyui-bridge/) 下对应 JSON 工作流配置文件中的节点定义。*

---

## 四、 本地 API 桥接层（comfyui-bridge）部署

本仓库在 `comfyui-bridge/` 中提供了面向 Web 端调用的 API 封装服务：

### 1. 配置文件部署
将 `comfyui-bridge` 目录放入目标运行路径（如 `~/WanderInk/comfyui-bridge`）。

### 2. 安装桥接服务依赖
```bash
conda activate comfyui
pip install flask requests websocket-client werkzeug pillow
```

### 3. 配置环境变量并启动 API 服务
```bash
cd ~/WanderInk/comfyui-bridge
# 配置后端 ComfyUI 的监听地址（默认为本地 8188）
export COMFYUI_SERVER="127.0.0.1:8188"
export COMFYUI_INPUT_DIR="/home1/wuzi/ComfyUI/input"
export SERVICE_PORT="5000"

# 启动统一的 API 桥接服务
python comfyui_api_service.py
```
启动后，WanderInk 后端或前端应用即可通过 `http://127.0.0.1:5000/api/music` 或 `/api/tts` 等端点进行免去复杂 Websocket 编码的直接生成与文件下载。

---

## 五、 后台常驻服务管理 (Systemd 配置)

为了实现无人值守、自启和自动崩溃恢复，强烈建议将 ComfyUI 主引擎配置为 **Systemd 用户级服务**。

### 1. 创建服务描述文件
在用户配置目录下新建服务文件（若目录不存在请手动创建）：
`~/.config/systemd/user/comfyui.service`

写入以下内容：
```ini
[Unit]
Description=ComfyUI User Service
After=network.target

[Service]
Type=simple
# 替换为你的真实 ComfyUI 根路径
WorkingDirectory=%h/ComfyUI
# 替换为你的 Anaconda 安装路径
ExecStart=%h/anaconda3/bin/conda run -n comfyui --no-capture-output python main.py --listen 0.0.0.0 --port 8188 --use-flash-attention --preview-method auto
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
```

### 2. 启动与常驻命令
在主账号终端（非 sudo）下依次运行：
```bash
# 重新加载 Systemd 用户级配置
systemctl --user daemon-reload

# 启用开机自启
systemctl --user enable comfyui

# 启动服务
systemctl --user start comfyui

# 查看实时运行日志
journalctl --user -u comfyui -f
```

配置成功后，ComfyUI 将常驻在 `http://127.0.0.1:8188` 后台提供渲染引擎支持。
