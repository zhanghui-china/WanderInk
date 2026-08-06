#!/usr/bin/env bash
# 部署到 DGX。把过去口口相传的流程焊死成一条命令,顺序不可调换。
#
# 为什么要有这个脚本:
#   1. 在途闸门。2026-07-28 我手敲部署时,检查命令确实输出了「在途: 1」,但它和 scp/重启
#      串在同一条命令链里、没有拦截,结果重启打断了一个正在跑 S4 的作品。闸门必须是
#      **能让脚本退出**的一步,不是一行打印。
#   2. 版本自证。原先的验证只有 `curl -w '%{http_code}'`,200 只证明服务活着,
#      不证明跑的是刚传上去的代码。最后一步比对 /api/version 的 sha 才算数。
#
# 用法:scripts/deploy-dgx.sh [--force]
#   --force  越过在途闸门(会打断正在跑的作业,慎用)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 默认走 cpolar 公网隧道。cpolar 的地址和端口会轮换,而 Tailscale 的内网地址是固定的,
# 传大文件也快得多,所以留一个口子:SHANHAI_DGX_HOST=huntun@100.x.y.z SHANHAI_DGX_PORT=22
HOST="${SHANHAI_DGX_HOST:-huntun@21.tcp.vip.cpolar.cn}"
# ConnectTimeout 从 25 提到 60:Tailscale 到 DGX 是**走 relay 而不是直连**,链路空闲断开后
# 重新打洞要十几秒,25 秒卡在边缘上——2026-08-06 部署时第 1 步就这么超时失败过一次。
# 放宽它不花代价:主机可达时该多快还是多快,变的只是"多久才放弃"。
SSH_OPTS=(-o ConnectTimeout="${SHANHAI_DGX_CONNECT_TIMEOUT:-60}" -p "${SHANHAI_DGX_PORT:-14801}")
REMOTE="~/shanhai"
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# ---- 1. 在途闸门(必须排第一,且必须能让脚本退出) ----
say "检查在途任务"
INFLIGHT=$(ssh "${SSH_OPTS[@]}" "$HOST" \
  "cd $REMOTE && grep -l '\"pipeline\": \"running\"\|\"pipeline\": \"queued\"' projects/*/project.json 2>/dev/null | wc -l" \
  | tr -d '[:space:]')
if [[ "$INFLIGHT" != "0" ]]; then
  ssh "${SSH_OPTS[@]}" "$HOST" "cd $REMOTE && grep -l '\"pipeline\": \"running\"\|\"pipeline\": \"queued\"' projects/*/project.json 2>/dev/null | xargs -n1 dirname | xargs -n1 basename"
  if [[ "$FORCE" == "0" ]]; then
    echo "有 $INFLIGHT 个作业在跑,重启会打断它们(已生成的产物不丢,但当前那一项白跑)。" >&2
    echo "确实要打断就加 --force。" >&2
    exit 1
  fi
  echo "⚠️  --force:将打断上述 $INFLIGHT 个在途作业"
fi

# ---- 2. 打版本戳(必须在 npm run build 之前) ----
say "打版本戳"
python3 scripts/stamp-version.py
SHA=$(python3 -c "import json;print(json.load(open('version.json'))['sha'])")

# ---- 3. 前端构建(此时才把版本烧进 dist) ----
say "构建前端"
(cd web && npm run build)

# ---- 4. 同步代码 ----
# 排除项沿用 docs/ops-dgx.md,外加 __pycache__(2026-07-27 踩过的 .pyc/mtime 陷阱:
# 旧 .pyc 会让 DGX 静默跑旧代码)。version.json 不在排除项里,要跟着过去。
say "同步代码"
# --progress:这两趟是整个脚本最慢的一步,不加它 rsync 全程一声不吭,看着像卡死。
# ⚠️ 别改成更清爽的 --info=progress2:那是 rsync 3.1+ 的参数,而 macOS 自带的是
# openrsync(自称"2.6.9 compatible"),遇到 --info 会直接吐 usage 退出。
rsync -az --delete --timeout=90 --progress \
  --exclude='.env' --exclude='projects' --exclude='config.json' --exclude='users.json' \
  --exclude='.venv' --exclude='web/node_modules' --exclude='web/dist' \
  --exclude='spike' --exclude='out' --exclude='.git' --exclude='__pycache__' \
  -e "ssh ${SSH_OPTS[*]}" ./ "$HOST:$REMOTE/"

# ---- 5. 同步前端产物(与代码分两次传,历史上正是这里断过一半) ----
say "同步前端产物"
rsync -az --delete --timeout=90 --progress -e "ssh ${SSH_OPTS[*]}" web/dist/ "$HOST:$REMOTE/web/dist/"

# ---- 6. 依赖 + 测试(失败即中止,不重启,让旧版继续服务) ----
say "远端 uv sync + pytest"
# ⚠️ `set -o pipefail` 必须写在**远端**这条命令里。本脚本开头的 `set -euo pipefail` 只管
# 本机;`pytest | tail -3` 这个管道跑在 ssh 的远端 shell 里,远端没有 pipefail 时退出码取
# tail 的(恒 0),ssh 便返回 0,本机 set -e 看到的是"成功"。
# 2026-07-28 实测踩到:DGX 上一条测试失败,脚本照样往下重启了服务——
# "测试失败即中止、不重启"这个保证当时是假的。
# ⚠️⚠️ 注释必须写在这里,**不能**插进 `ssh ... \` 与后面那个字符串之间:续行符后跟注释
# 会把命令就地截断,ssh 变成"不带命令地登录一次",随后那个字符串被当成可执行文件名。
# 同一天第二次踩到,所以留这条。
REMOTE_TEST="set -o pipefail; export PATH=\$HOME/.local/bin:\$PATH; cd $REMOTE && uv sync -q && uv run pytest -q 2>&1 | tail -3"
ssh "${SSH_OPTS[@]}" "$HOST" "$REMOTE_TEST"

# ---- 7. 重启 ----
say "重启 shanhai-web"
ssh "${SSH_OPTS[@]}" "$HOST" "systemctl --user restart shanhai-web && sleep 8"

# ---- 8. 自证:线上跑的确实是刚传的这一版 ----
say "校验线上版本"
LIVE=$(ssh "${SSH_OPTS[@]}" "$HOST" \
  "curl -s --max-time 10 http://127.0.0.1:5000/api/version" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['sha'])")
if [[ "$LIVE" != "$SHA" ]]; then
  echo "❌ 线上版本 $LIVE 与本次部署 $SHA 不一致——部署没生效,或服务没真正重启" >&2
  exit 1
fi
echo "✅ 线上 = $LIVE,与本次部署一致"
