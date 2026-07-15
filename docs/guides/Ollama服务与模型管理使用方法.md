# Ollama 后台服务与模型管理使用方法

目前，系统已为您配置好 Ollama 的后台守护进程，以及**开机自动载入 GLM 模型到显卡**的用户级服务。本手册介绍如何管理和使用它们。

---

## 一、 Ollama 服务运行说明
1. **系统全局自启**：Ollama 已经在系统级别作为全局服务自启，占用端口 `11434`。
2. **无需手动启动**：切勿在终端输入 `ollama serve`，否则会产生端口冲突报错。
3. **接口地址**：任何第三方应用或 WebUI 想要对接 Ollama 时，请填写：`http://127.0.0.1:11434`。

---

## 二、 GLM 模型开机自动预加载服务 (Systemd 用户级服务)

为了避免每次使用模型时重新加载导致的首字延迟，我们创建了用户级服务 `ollama-preload`。
它会在**开机或用户登录时延迟 10 秒**，自动发送请求将 `glm-4.7-flash:latest` 模型载入显存并永久常驻（配置为 `keep_alive: -1`）。

以下是该服务的管理命令（**均无需 sudo 权限**）：

* **查看预加载服务运行状态**：
  ```bash
  systemctl --user status ollama-preload
  ```
* **查看加载日志**（用于确认是否加载成功）：
  ```bash
  journalctl --user -u ollama-preload -n 20
  ```
* **手动立即触发一次预加载**：
  ```bash
  systemctl --user start ollama-preload
  ```
* **禁用开机自动预加载**：
  ```bash
  systemctl --user disable ollama-preload
  ```
* **重新启用开机自动预加载**：
  ```bash
  systemctl --user enable ollama-preload
  ```

---

## 三、 Ollama 常用命令及显存管理

### 1. 查看当前已载入显存的模型（GPU占满情况）
```bash
ollama ps
```
*如果输出有模型，说明该模型已占用 GPU 显存，处于热激活状态。*

### 2. 查看已安装的所有模型列表
```bash
ollama list
```

### 3. 命令行交互对话
```bash
ollama run glm-4.7-flash:latest
```

### 4. 手动释放显存（卸载模型）
如果您需要运行 ComfyUI 或其他大模型，需要临时释放被 GLM 模型占用的显存，可以在终端直接运行此命令（已避开本地代理限制）：
```bash
curl --noproxy "*" -X POST http://127.0.0.1:11434/api/generate -d '{"model": "glm-4.7-flash:latest", "keep_alive": 0}'
```
*运行后，使用 `ollama ps` 可以看到模型已被卸载，GPU 显存得到释放。*

### 5. 手动重新常驻加载显存
如果您想再次将模型永久加载回显存：
```bash
curl --noproxy "*" -X POST http://127.0.0.1:11434/api/generate -d '{"model": "glm-4.7-flash:latest", "keep_alive": -1}'
```
