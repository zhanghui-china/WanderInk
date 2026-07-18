# src/shanhai/loras.py
"""ComfyUI 本地图像生成可选的 LoRA 模型:短名 -> 五子 image-shim 实际认得的
safetensors 文件名。新增一个 LoRA 只需要在这里加一行,/api/meta 的 loras 列表
和配置面板下拉框会自动跟着变,不用改前端代码。"""
LORA_PRESETS = {
    "Real_Ani": "Real_Ani-Qwen_000001250.safetensors",
    "figurine": "figurine_qwen.safetensors",
}
