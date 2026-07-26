# src/shanhai/loras.py
"""ComfyUI 本地图像生成可选的 LoRA 短名,取值与 DGX 上 image-shim 认的完全一致
(不区分大小写)。短名→safetensors 文件名的映射放在 shim 侧,shanhai 只认短名——
两边共用一套词汇,将来若改调队友那套服务可无缝切换。

新增一个 LoRA 只需要在这里加一行:/api/meta 的 loras 列表与配置面板下拉框自动跟着变。

注意:LoRA 只对漫画页(S4,走 /images/edits)生效——角色三视图(S3)走的
Text2IMGKrea2 工作流里没有 LoRA 节点,选了也不会有效果。"""
LORA_PRESETS = ("Real_ani_qwen", "figurine_qwen", "bjd.7ARL")
