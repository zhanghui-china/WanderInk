# WanderInk

给定一个景区名称，系统自动生成历史故事、剧本、分镜、角色设计、漫画画面、配音配乐，最终合成一部**有声连环画**——可在 NVIDIA DGX Spark 单机上跑通全链路。

> 详细产品方案见 [docs/product/](docs/product/景区有声连环画%20Agent%20—%20产品方案（优化版）.md)

## Demo



> **Demo 视频（待补充）**
>
> - 在线播放：`（粘贴视频链接，如 GitHub Release / Bilibili / YouTube）`
> - 或把文件放到本仓库后引用，例如：`docs/demo/wanderink-demo.mp4`
>
> ```html
> <!-- 可选：HTML 嵌入占位
> <video src="docs/demo/wanderink-demo.mp4" controls width="720"></video>
> -->
> ```

## 一句话 Pitch

**景区名称进 → 有声连环画（MP4）出**，端到端自动化，支持分步预览与重生成。

## 流水线（S0–S6）

```
输入景区名
  → [S0] 传说检索与甄别
  → [S1] 剧本改编
  → [S2] 分镜设计
  → [S3] 角色设定（三视图，锁定外观一致性）
  → [S4] 连环画页生成
  → [S5] 配音 + 配乐
  → [S6] 合成输出 MP4
```

## 快速开始

依赖：**Python ≥ 3.12**、[uv](https://github.com/astral-sh/uv)、前端用 [bun](https://bun.sh)。

```bash
cd web
cp .env.example .env   # 按环境填写端点与模型
uv sync
uv run shanhai-web     # 后端 :8080

cd web/web
bun install && bun run dev   # 前端 :5173
```

ComfyUI 桥接（可选）：

```bash
cd comfyui-bridge
python comfyui_api_service.py
```

更细的部署与运维见下方文档索引。

## 目录结构

```
WanderInk/
├── docs/
│   ├── product/          # 产品方案
│   ├── guides/           # DGX / Ollama / ComfyUI 运维手册
│   └── demo/             # （可选）放置 Demo 视频文件
├── web/                  # 主应用（FastAPI + React）
│   ├── src/shanhai/      # 后端流水线 S0–S6
│   ├── web/              # 前端
│   ├── assets/           # 字体、BGM 等
│   └── docs/             # PRD、部署与决策记录
└── comfyui-bridge/       # ComfyUI HTTP 桥接
    ├── workflows/        # 图像 / TTS / 音乐 JSON 模板
    └── test/             # CLI 测试脚本
```

## 文档索引


| 位置                                                   | 内容                        |
| ---------------------------------------------------- | ------------------------- |
| [docs/product/](docs/product/)                       | 产品方案（优化版）                 |
| [docs/guides/](docs/guides/)                         | ComfyUI / Ollama / DGX 运维 |
| [web/docs/](web/docs/)                               | PRD、决策记录                  |
| [web/docs/deploy-dgx.md](web/docs/deploy-dgx.md)     | DGX 部署说明                  |
| [web/.env.example](web/.env.example)                 | 环境变量模板                    |
| [comfyui-bridge/README.md](comfyui-bridge/README.md) | 桥接服务说明                    |


