#!/usr/bin/env bash
set -Eeuo pipefail

export PATH="$HOME/.local/bin:$PATH"

project_dir="${HIKARI_BOT_DIR:-$HOME/hikari-bot}"

cd "$project_dir"
git fetch origin main
git reset --hard origin/main
git clean -fd
uv sync --no-dev
if systemctl cat jihuanshe-bridge.service >/dev/null 2>&1; then
    sudo systemctl restart jihuanshe-bridge.service
fi
sudo systemctl restart bot.service
