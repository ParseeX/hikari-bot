#!/usr/bin/env bash
set -Eeuo pipefail

project_dir="${HIKARI_BOT_DIR:-$HOME/hikari-bot}"

cd "$project_dir"
git fetch origin main
git reset --hard origin/main
git clean -fd
uv sync --no-dev
sudo systemctl restart bot.service
