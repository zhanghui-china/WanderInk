# DGX 运维速查:启动 / 停止 / 查日志 / 常见维护

> 这是操作速查表,不是变更记录——"为什么改成这样"的历史背景见 [deploy-dgx.md](deploy-dgx.md)(按时间倒序的部署日志)。
> 前置:SSH 到 DGX 用 `ssh -p 14801 huntun@21.tcp.vip.cpolar.cn`(cpolar 内网穿透隧道)。

## 服务清单

一共 4 个 systemd **user** 服务(不是系统级服务,别加 `sudo`,命令里也不用 `--system`):

| 服务名 | 端口 | 作用 | 依赖 |
|---|---|---|---|
| `shanhai-web` | 5000 | 主站(FastAPI + 前端 SPA),真正对外的入口 | 无(本身不依赖其它三个才能启动,但生成时会调用它们) |
| `shanhai-image` | 8091 | 图像生成 shim,把 OpenAI 兼容接口转发给本机 ComfyUI(:8188) | ComfyUI 进程(队友 wuzi 维护,见下方"8091 图像服务"一节) |
| `shanhai-tts` | 8090 | 配音 shim(Qwen3-TTS VoiceDesign),同样经 ComfyUI | 同上,ComfyUI |
| `shanhai-music` | 8092 | 配乐 shim(ACE-Step),同样经 ComfyUI | 同上,ComfyUI |

image/tts/music 三个都是"薄壳"(shim):自己只做协议转换,真正算力都在 ComfyUI(`127.0.0.1:8188`)——这个 ComfyUI 进程**不是** shanhai 的服务,由另一个系统用户 `wuzi` 独立维护,`huntun` 这个账号既没有 sudo 也没有权限直接管它,这三个 shim 挂了/超时,先查 ComfyUI 是不是活的(见下)。

## 启动 / 停止 / 重启 / 看状态

```bash
# 全部一起
systemctl --user start   shanhai-web shanhai-tts shanhai-image shanhai-music
systemctl --user stop    shanhai-web shanhai-tts shanhai-image shanhai-music
systemctl --user restart shanhai-web shanhai-tts shanhai-image shanhai-music

# 单个(把服务名换掉即可)
systemctl --user status  shanhai-web
systemctl --user restart shanhai-web
```

`status` 输出里关注 `Active:` 那行——`active (running)` 才是正常;`inactive (dead)` 或 `failed` 需要 `start`/`restart`。

**只改了 shanhai 主站代码**(前端/后端)时只需要 `restart shanhai-web` 一个;image/tts/music 三个 shim 是独立仓库/独立 venv,不受 shanhai 代码改动影响,不用跟着重启。

**`.env` 改了才需要重启 `shanhai-web`**——`EnvironmentFile=%h/shanhai/.env` 只在进程启动时读一次,改完 `.env` 不重启不会生效。

## 查看日志

```bash
journalctl --user -u shanhai-web -f          # 实时跟随(Ctrl-C 退出)
journalctl --user -u shanhai-web -n 200      # 最近 200 行
journalctl --user -u shanhai-web --since "10 min ago"
journalctl --user -u shanhai-web -p err      # 只看 error 级别

# 把服务名换成 shanhai-image/shanhai-tts/shanhai-music 同样适用
```

## 健康检查

```bash
curl http://127.0.0.1:5000/           # 主站:200 即正常(SPA 首页)
curl http://127.0.0.1:8091/health     # 图像 shim:{"ok":true}
curl http://127.0.0.1:8090/health     # 配音 shim:{"status":"ok"}
curl http://127.0.0.1:8092/health     # 配乐 shim:{"status":"ok"}
curl http://127.0.0.1:8188/system_stats   # ComfyUI 本尊:200 说明它还活着
```

> **三个 shim 的源码存档**:`~/image-shim`、`~/qwentts-shim`、`~/music-shim` 的 `main.py`
> 都不在版本控制里,但仓库的 [`scripts/dgx-shims/`](../scripts/dgx-shims/) 存了一份**副本**,
> 便于查阅、review 与机器重装时恢复。注意那是副本不是真源——**改仓库里那份不会有任何效果**,
> 必须 scp 回 DGX 并重启对应服务,同步方法见该目录的 README。

## 8091 图像服务(shanhai-image)专项

- 本体是 `~/image-shim/main.py`(独立 venv,不在 shanhai git 仓库里),用 `uvicorn main:app --port 8091` 常驻。它自己不做任何生成计算,只是把 shanhai 发来的 `POST /v1/images/generations` 请求转换成 ComfyUI 的 websocket 排队协议,再把结果转回 OpenAI 风格的响应。
- **图像生不出来 / 一直超时,先分层排查**:
  1. `curl :8091/health` 不通 → shim 自己挂了,`systemctl --user restart shanhai-image`,再看 `journalctl --user -u shanhai-image -n 100` 找报错。
  2. `:8091/health` 通,但实际生图失败/超时 → 大概率是 ComfyUI(`:8188`)本身的问题,`curl :8188/system_stats`:
     - 连不上/超时 → ComfyUI 进程不在了,这个**只能找 wuzi 本人重新拉起**,`huntun` 账号没权限管他的进程。
     - 通但生图仍失败 → 看 ComfyUI 自己的日志(需要 wuzi 配合,或者检查 `~image-shim` 侧日志里贴出的 ComfyUI 报错信息)。
- **DGX 整机重启后的已知坑**:即便 `shanhai-*` 四个服务现在 `Linger=yes`(能自启,`loginctl show-user huntun` 可验证),wuzi 的 ComfyUI **不会跟着自启**——DGX 重启后哪怕 shanhai 四个服务都 `active`,图像/配音/配乐仍可能全部失败,记得额外 `curl :8188/system_stats` 确认一次,不通就去找 wuzi。
- 如果需要临时把图像生成切走(比如 ComfyUI 一直起不来),去 Web 的"配置"面板,把"图像生成"整组 Base URL/模型改成云端端点(如之前用过的 tu-zi),或者点"清除(改为继承)"切回 `.env` 里配置的默认值——这是纯前端配置操作,不涉及重启服务。

## 常见维护场景

**部署新代码**——用脚本,不要手敲:
```bash
scripts/deploy-dgx.sh            # 有在途任务会直接拒绝
scripts/deploy-dgx.sh --force    # 确实要打断在途任务时才用
```
脚本按顺序做八件事:在途闸门 → 打版本戳(`version.json`)→ `npm run build`(把版本烧进 dist)
→ rsync 代码 → rsync `web/dist` → 远端 `uv sync` + `pytest`(失败即中止**且不重启**,旧版继续服务)
→ 重启 → **校验 `/api/version` 的 sha 与本次部署一致**。

两条为什么必须是脚本而不是手敲的记录:
- 2026-07-28 手敲部署时,在途检查确实输出了「在途: 1」,但它和 scp/重启串在同一条命令链里、
  没有拦截能力,结果打断了一个正在跑 S4 的作品。**闸门必须能让流程退出。**
- 原先的验证只有 `curl -w '%{http_code}'`,200 只证明服务活着,不证明跑的是刚传上去的代码。
  最后那步 sha 比对才算数——历史上发生过 rsync 代码超时中断而 dist 成功,线上成了新前端 + 旧后端。

前端固定在本机构建(DGX 上没有 node_modules/tsgo),脚本已包含这一步。
页脚会同时显示前端与后端的构建号,不一致时标红——线上到底部署成没成,看一眼页脚即可。

**DGX 整机重启后的兜底**(即便 linger 已开,出问题时的手动补救):
```bash
systemctl --user start shanhai-web shanhai-tts shanhai-image shanhai-music
curl http://127.0.0.1:8188/system_stats   # 200 说明 ComfyUI 也活着;不通则联系 wuzi
```

**磁盘/项目数据**:项目数据在 `~/shanhai/projects/`(每个作品一个目录,含 `project.json` + 生成的图片/音频/成片),配置在 `~/shanhai/config.json`(Web 配置面板改的内容落这里),账号在 `~/shanhai/users.json`。这三者都不进 git、不随 rsync 部署覆盖(部署命令里显式 `--exclude`),线上数据安全。

## 访问地址

- 局域网直连:`http://<DGX 局域网 IP>:5000`(IP 走 DHCP 会变,DGX 上 `hostname -I` 现查为准,不要死记旧 IP)。
- 团队公网访问走 cpolar 隧道(具体域名/端口由维护 cpolar 的人提供)。
