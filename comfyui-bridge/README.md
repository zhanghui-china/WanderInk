# ComfyUI API Bridge

对接和调度 ComfyUI 图像与音频生成的 HTTP 桥接服务。

## 目录结构

```
comfyui-bridge/
├── comfyui_api_service.py   # 统一多模态 API（Flask）
├── comfyui_edit_service.py  # 单图编辑接口
├── workflows/               # ComfyUI 工作流 JSON 模板
└── test/                    # CLI 调用与生成测试脚本
```

## 启动服务

```bash
python comfyui_api_service.py
```

工作流模板默认从 `workflows/` 读取；也可用环境变量 `WORKFLOW_PATH` 覆盖编辑服务模板路径。

安装与部署说明见仓库文档：[docs/guides/ComfyUI安装与部署手册.md](../docs/guides/ComfyUI安装与部署手册.md)
