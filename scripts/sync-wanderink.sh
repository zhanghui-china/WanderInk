#!/usr/bin/env bash
# 把 shanhai 的最新提交同步进 WanderInk 仓库的 web/ 子目录(git subtree,保留完整历史)并推送。
#
# 用法: ./scripts/sync-wanderink.sh
#
# 前提: shanhai 本地(即本仓库)已提交好要发布的改动,在 main 分支。
# 效果: 若 ~/Work/WanderInk 不存在则先 clone;否则 fetch 对齐远端;
#       然后 subtree pull 把 shanhai/main 合进 web/,再 push 到 WanderInk 的 main。
#
# 必须由用户在自己终端里跑——Claude Code 的自动模式分类器会拦截这类跨仓库 push
# (判定为潜在数据外泄),不能代为执行。
set -euo pipefail

SHANHAI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WANDERINK_DIR="${WANDERINK_DIR:-$HOME/Work/WanderInk}"
WANDERINK_URL="https://github.com/zhanghui-china/WanderInk.git"

if [ ! -d "$WANDERINK_DIR/.git" ]; then
    echo "未找到 WanderInk 本地副本,clone 到 $WANDERINK_DIR ..."
    git clone "$WANDERINK_URL" "$WANDERINK_DIR"
fi

cd "$WANDERINK_DIR"
git fetch origin
git checkout main
git merge --ff-only origin/main

MSG="sync web/ from shanhai $(cd "$SHANHAI_DIR" && git rev-parse --short HEAD)"
git subtree pull --prefix=web "$SHANHAI_DIR" main -m "$MSG"
git push origin main

echo "已同步并推送: $WANDERINK_URL"
