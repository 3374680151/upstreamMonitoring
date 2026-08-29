#!/usr/bin/env bash
# Create a sibling git worktree for a parallel agent session:
# new business branch from master + .env copy + web dependencies.
# Usage: bash scripts/new_worktree.sh <business-name>
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "用法：bash scripts/new_worktree.sh <业务名>（全小写 + 连字符，如 channel-auto-demotion）" >&2
  exit 1
fi

name="$1"
if ! [[ "$name" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]]; then
  echo "分支名必须是全小写 + 连字符的纯业务名，如 channel-auto-demotion" >&2
  exit 1
fi

root="$(git rev-parse --show-toplevel)"
worktree_path="$(dirname "$root")/upstream-$name"

git -C "$root" worktree add "$worktree_path" -b "$name" master

# .env 不入库，各工作区自备一份
if [[ -f "$root/.env" ]]; then
  cp "$root/.env" "$worktree_path/.env"
else
  echo "警告：根目录没有 .env，请手动创建 $worktree_path/.env" >&2
fi

# node_modules 不共享，新工作区需要单独安装
if ! (cd "$worktree_path/apps/web" && npm install); then
  echo "警告：npm install 失败，请稍后在 $worktree_path/apps/web 手动重试" >&2
fi

echo
# 花括号必须有：macOS 自带 bash 3.2 会把紧跟变量名的全角字符（如（，）吞进变量名
echo "worktree 就绪：${worktree_path}（分支 ${name}，基于 master）"
echo "在新的 ZCode 会话里打开该目录即可并行开发；验证端口与主目录错开（如 8020 / 8030）。"
