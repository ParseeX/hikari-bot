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
    assert 'export PATH="$HOME/.local/bin:$PATH"' in script
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
