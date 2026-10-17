#!/usr/bin/env bash
set -Eeuo pipefail

export PATH="$HOME/.local/bin:$PATH"

project_dir="${HIKARI_BOT_DIR:-$HOME/hikari-bot}"

cd "$project_dir"
previous_revision="$(git rev-parse HEAD)"
git fetch origin main
# 保留线上尚未提交的文件；分支分叉或文件冲突时停止部署。
git merge --ff-only origin/main
uv sync --no-dev
if systemctl cat jihuanshe-bridge.service >/dev/null 2>&1; then
    # 只有桥接运行代码改变或服务未运行时才重启，普通 bot 更新保留手机会话。
    if ! git diff --quiet "$previous_revision" HEAD -- \
        'scripts/jihuanshe_bridge/*.py' 'scripts/jihuanshe_bridge/*.js' \
        'scripts/jihuanshe_bridge/*.png' 'scripts/jihuanshe_bridge/*.service' \
        'scripts/jihuanshe_bridge/requirements.txt' \
        || ! systemctl is-active --quiet jihuanshe-bridge.service; then
        sudo systemctl restart jihuanshe-bridge.service
    fi
fi
sudo systemctl restart bot.service
