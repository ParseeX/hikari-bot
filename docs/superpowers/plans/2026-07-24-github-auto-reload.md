# GitHub Push 自动重载插件 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `main` 分支 push 后通过 GitHub Actions SSH 到生产服务器，更新 hikari-bot 并重启 `bot.service`。

**Architecture:** GitHub Actions 仅负责监听 `main` push、注入 SSH Secrets 并调用服务器上的 `update.sh`。`update.sh` 在 `$HOME/hikari-bot`（或 `HIKARI_BOT_DIR` 指定目录）中同步 `origin/main`、同步生产依赖，然后通过 systemd 重启 `bot.service`。README 记录 Secrets、服务器脚本和 sudoers 前置配置。

**Tech Stack:** GitHub Actions, `appleboy/ssh-action@v1.2.0`, Bash, Git, uv, systemd, pytest。

## Global Constraints

- 只监听 `main` 分支的 push。
- 使用 `SERVER_HOST`、`SERVER_USER`、`SERVER_SSH_KEY`，并支持可选 `SERVER_PORT` Secret。
- 远端默认脚本路径是 `$HOME/hikari-bot/update.sh`。
- 远端默认项目目录是 `$HOME/hikari-bot`，可由 `HIKARI_BOT_DIR` 覆盖。
- systemd 服务名必须是 `bot.service`。
- 生产 `.env`、SSH 私钥和业务凭据不得提交到仓库。
- `update.sh` 必须在更新失败时停止，不得在依赖同步失败后重启 bot。

---

### Task 1: Add failing deployment contract tests

**Files:**
- Create: `tests/test_deployment_config.py`

**Interfaces:**
- Consumes: Repository files that will be created or updated in later tasks.
- Produces: Three contract tests covering the workflow trigger/SSH configuration, update script commands, and README setup instructions.

- [ ] **Step 1: Write the failing tests**

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_workflow_deploys_main_push_over_ssh():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    assert "branches:" in workflow
    assert "      - main" in workflow
    assert "appleboy/ssh-action@v1.2.0" in workflow
    assert "SERVER_HOST" in workflow
    assert "SERVER_USER" in workflow
    assert "SERVER_SSH_KEY" in workflow
    assert "SERVER_PORT" in workflow
    assert "script_stop: true" in workflow
    assert 'bash "$HOME/hikari-bot/update.sh"' in workflow


def test_update_script_syncs_code_dependencies_and_bot_service():
    script = (ROOT / "update.sh").read_text(encoding="utf-8")

    assert script.startswith("#!/usr/bin/env bash")
    assert "set -Eeuo pipefail" in script
    assert "HIKARI_BOT_DIR:-$HOME/hikari-bot" in script
    assert "git fetch origin main" in script
    assert "git reset --hard origin/main" in script
    assert "git clean -fd" in script
    assert "uv sync --no-dev" in script
    assert "sudo systemctl restart bot.service" in script


def test_readme_documents_required_deployment_secrets():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for name in ("SERVER_HOST", "SERVER_USER", "SERVER_SSH_KEY", "SERVER_PORT"):
        assert name in readme
    assert "bot.service" in readme
    assert "update.sh" in readme
```

- [ ] **Step 2: Run the new tests to verify they fail for missing deployment files**

Run: `uv run pytest tests/test_deployment_config.py -q`

Expected: FAIL because `.github/workflows/deploy.yml` and `update.sh` do not exist yet, and README does not document the deployment Secrets.

### Task 2: Add the SSH deployment workflow and remote update script

**Files:**
- Create: `.github/workflows/deploy.yml`
- Create: `update.sh`

**Interfaces:**
- Consumes: GitHub Secrets `SERVER_HOST`, `SERVER_USER`, `SERVER_SSH_KEY`, optional `SERVER_PORT`.
- Produces: A push-triggered job that invokes `$HOME/hikari-bot/update.sh`; an executable server script that updates `origin/main`, syncs production dependencies, and restarts `bot.service`.

- [ ] **Step 1: Add the workflow**

Create `.github/workflows/deploy.yml` with:

```yaml
name: Trigger Server Deploy

on:
  push:
    branches:
      - main

jobs:
  deploy:
    runs-on: ubuntu-latest

    steps:
      - name: Run deploy script on server
        uses: appleboy/ssh-action@v1.2.0
        with:
          host: ${{ secrets.SERVER_HOST }}
          username: ${{ secrets.SERVER_USER }}
          key: ${{ secrets.SERVER_SSH_KEY }}
          port: ${{ secrets.SERVER_PORT || 22 }}
          script_stop: true
          script: bash "$HOME/hikari-bot/update.sh"
```

- [ ] **Step 2: Add the update script**

Create `update.sh` with:

```bash
#!/usr/bin/env bash
set -Eeuo pipefail

project_dir="${HIKARI_BOT_DIR:-$HOME/hikari-bot}"

cd "$project_dir"
git fetch origin main
git reset --hard origin/main
git clean -fd
uv sync --no-dev
sudo systemctl restart bot.service
```

Ensure the script is executable in Git by running:

```powershell
git update-index --chmod=+x update.sh
```

- [ ] **Step 3: Run the contract tests to verify the implementation passes**

Run: `uv run pytest tests/test_deployment_config.py -q`

Expected: PASS with `3 passed`.

### Task 3: Document server and GitHub configuration

**Files:**
- Modify: `README.md` after the production environment configuration section.

**Interfaces:**
- Consumes: The workflow Secrets and script/service names from Task 2.
- Produces: Operator instructions for configuring GitHub Secrets, installing `update.sh` on the server, and allowing the SSH user to restart only `bot.service`.

- [ ] **Step 1: Add the deployment section**

Add a section that documents the following exact setup:

```markdown
## GitHub Actions 自动部署

`main` 分支 push 后，GitHub Actions 会通过 SSH 执行服务器上的
`$HOME/hikari-bot/update.sh`。请在仓库 Settings → Secrets and variables → Actions
中配置：

- `SERVER_HOST`：服务器地址
- `SERVER_USER`：SSH 用户
- `SERVER_SSH_KEY`：SSH 私钥
- `SERVER_PORT`：可选 SSH 端口，未配置时使用 22

服务器上需要保证以下文件存在并可执行：

```text
$HOME/hikari-bot/update.sh
```

脚本默认进入 `$HOME/hikari-bot`，也支持通过 `HIKARI_BOT_DIR` 指定其他项目目录；
它会同步 `origin/main`、执行 `uv sync --no-dev`，并重启 `bot.service`。
SSH 用户需要被授予执行 `sudo systemctl restart bot.service` 的权限。
```

- [ ] **Step 2: Run the contract tests again**

Run: `uv run pytest tests/test_deployment_config.py -q`

Expected: PASS with `3 passed`.

### Task 4: Verify the complete change

**Files:**
- Verify: `.github/workflows/deploy.yml`
- Verify: `update.sh`
- Verify: `README.md`
- Verify: `tests/test_deployment_config.py`

**Interfaces:**
- Consumes: All files from Tasks 1–3.
- Produces: Evidence that shell syntax, deployment contract tests, dependency lock consistency, and the full Python test suite pass.

- [ ] **Step 1: Check the workflow and shell syntax**

Run:

```powershell
uv run python -c "from pathlib import Path; p=Path('.github/workflows/deploy.yml'); assert p.is_file(); assert 'appleboy/ssh-action@v1.2.0' in p.read_text(encoding='utf-8')"
bash -n update.sh
```

Expected: Both commands exit with code 0 and print no errors.

- [ ] **Step 2: Check the lock file and run all tests**

Run:

```powershell
uv lock --check
uv run pytest -q
```

Expected: `uv lock --check` succeeds and pytest reports zero failures.

- [ ] **Step 3: Review the final diff and status**

Run: `git diff --check`

Run: `git status --short`

Run: `git diff -- .github/workflows/deploy.yml update.sh README.md tests/test_deployment_config.py`

Expected: no whitespace errors; only the intended deployment workflow, script, README section, and contract tests are changed.

- [ ] **Step 4: Commit the implementation**

```bash
git add .github/workflows/deploy.yml update.sh README.md tests/test_deployment_config.py
git commit -m "feat: deploy bot on main push"
```
