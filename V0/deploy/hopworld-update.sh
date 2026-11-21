#!/usr/bin/env bash
# Update the systemd installation from its clean Git main branch.
set -euo pipefail
umask 022
exec 9>/var/lock/hopworld-router-update.lock
flock -n 9 || { echo '另一个更新正在运行。' >&2; exit 1; }

cd /opt/hopworld-router
test -d .git || { echo '此目录不是 Git 安装，不能自动更新。' >&2; exit 1; }
test -x .venv/bin/python || { echo '缺少 Python 虚拟环境。' >&2; exit 1; }
test "$(git symbolic-ref --short HEAD)" = main || { echo '仅支持更新 main 分支。' >&2; exit 1; }
test -z "$(git status --porcelain)" || { echo '项目目录有未提交或未跟踪的文件，请先处理。' >&2; exit 1; }
systemctl is-active --quiet hopworld-router || { echo '服务未运行，请先检查服务。' >&2; exit 1; }

git fetch origin main
old=$(git rev-parse HEAD)
target=$(git rev-parse FETCH_HEAD)
test "$(git merge-base "$old" "$target")" = "$old" || { echo '本地版本无法快进，请手动处理。' >&2; exit 1; }
if test "$old" = "$target"; then
  echo "已是最新版本：$old"
  exit 0
fi

state=/var/lib/hopworld-router-updates
new_venv=
requirements=$(mktemp)
trap 'rm -f "$requirements"' EXIT
git show "$target:requirements.txt" > "$requirements"
if ! cmp -s requirements.txt "$requirements"; then
  install -d -m 755 "$state/venvs" "$state/previous"
  new_venv=$(mktemp -d "$state/venvs/venv.XXXXXXXX")
  chmod 755 "$new_venv"
  .venv/bin/python -m venv "$new_venv"
  "$new_venv/bin/pip" install -r "$requirements"
fi

test -z "$(git status --porcelain)" || { echo '准备更新期间项目发生变化，已取消。' >&2; exit 1; }
git merge --ff-only "$target"
if test -n "$new_venv"; then
  previous=$(mktemp -d "$state/previous/venv.XXXXXXXX")
  rmdir "$previous"
  mv .venv "$previous"
  ln -s "$new_venv" .venv
  echo "原虚拟环境已保留：$previous"
fi
if ! systemctl restart hopworld-router; then
  echo "服务重启失败；原代码版本 $old，当前版本 $target。请检查日志并使用备份恢复。" >&2
  exit 1
fi
sleep 5
if ! systemctl is-active --quiet hopworld-router; then
  echo "服务重启失败；原代码版本 $old，当前版本 $target。请检查日志并使用备份恢复。" >&2
  exit 1
fi
echo "更新完成：$old -> $target"
