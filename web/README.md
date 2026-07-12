# WanderInk web

WanderInk 有声连环画生成器的 web 前端(React + Vite + Tailwind),后端为 `src/shanhai/api.py`(FastAPI)。

## 本地开发

类型检查用 TypeScript 7 原生编译器 tsgo(@typescript/native-preview),Vite/esbuild 负责转译。

两个进程:

```bash
# 1) 后端(仓库根目录),:8080
uv run shanhai-web

# 2) 前端(web/),:5173,已代理 /api 与 /files 到 :8080
cd web && bun install && bun run dev
```

打开 http://localhost:5173 。

## 生产 / 一体化

```bash
cd web && bun run build     # 产出 web/dist
uv run shanhai-web          # api.py 检测到 web/dist 存在,挂到 / 直接托管
```
此时前后端同源,访问 http://127.0.0.1:8080 即可。

## 本机编辑实例

编辑和重生成功能仅在本机非只读实例可用。启动本机编辑实例:

```bash
SHANHAI_PORT=8081 uv run shanhai-web  # 不设 SHANHAI_READONLY
```

访问 http://127.0.0.1:8081 可看到编辑控件。公网只读实例(默认 :10000)会自动隐藏编辑控件,且所有编辑端点返回 403。两个实例共用同一份 `projects/` 和已编译的 `web/dist`。

## 交给 Claude Design 改

前端是标准 React + Tailwind。把仓库 `web/` 目录地址交给 Claude Design,可直接改 `src/components/*` 与 `src/App.tsx`。约定别动的接口:

- 数据来自 `src/api.ts`(`/api/meta`、`/api/projects`、`/api/projects/:id`),类型在 `src/types.ts`。
- 图片/音频/视频用后端返回的 URL 字段(`page.image`、`page.audio`、`character.image`、`project.mp4`),都是 `/files/...` 形态。
- 生成是异步的:创建后轮询 `project.pipeline`(queued/running/done/error)与 `project.status`(各步 s0–s6)。
- 编辑端点(修改、删除、重排序 cells;重生成 cells、characters)见 `src/shanhai/api.py`(PATCH/POST/DELETE `/api/projects/:id/cells/*`)。
