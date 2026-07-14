# ComfyUI API Bridge

本目录包含了用于对接和调度 ComfyUI 图像与音频生成服务的核心 API 服务端代码和测试 CLI 脚本。

## 目录结构

- `comfyui_api_service.py` - 统一的多模态 API 服务端代码 (Flask)
- `comfyui_edit_service.py` - 单图编辑接口服务
- `test/` - CLI 接口调用与生成测试脚本
- `*.json` - 对应的 ComfyUI 工作流 API 配置文件 (Templates)

## 启动服务

```bash
python comfyui_api_service.py
```
