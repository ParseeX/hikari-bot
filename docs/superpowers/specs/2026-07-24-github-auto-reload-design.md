# GitHub Push 自动重载插件设计

## 目标

当 `main` 分支有新的 push 时，GitHub Actions 通过 SSH 登录生产服务器，更新
`hikari-bot` 代码和生产依赖，并重启 `bot.service`，使新插件自动生效。

## 背景与约束

- 当前仓库没有 GitHub Actions workflow。
- 现有 QQ“重载插件”指令会执行 `git reset --hard`、`git clean -fd`、`git pull`
  后退出进程；生产环境由 systemd 的 `bot.service` 负责自动拉起。
- 生产服务器上的 `.env.prod` 不提交到仓库，GitHub Actions 不接触业务密钥。
- 生产环境使用 `uv` 管理 Python 依赖，启动入口为 `uv run python bot.py`。
- 部署入口采用 SSH，参考实现使用 `appleboy/ssh-action@v1.2.0`。

## 方案

新增两个部署相关文件：

1. `.github/workflows/deploy.yml`：只负责监听 `main` push、建立 SSH 连接并调用服务器脚本。
2. `update.sh`：只负责服务器上的更新和重启流程。

workflow 使用以下 GitHub Secrets：

- `SERVER_HOST`：生产服务器地址。
- `SERVER_USER`：SSH 登录用户。
- `SERVER_SSH_KEY`：该用户的 SSH 私钥。
- `SERVER_PORT`：可选 SSH 端口，未配置时使用 `22`。

服务器脚本默认使用 `$HOME/hikari-bot` 作为项目目录，也允许通过
`HIKARI_BOT_DIR` 覆盖。脚本完成更新后执行：

```bash
sudo systemctl restart bot.service
```

## 数据流

```text
push main
  -> GitHub Actions
  -> appleboy/ssh-action
  -> $HOME/hikari-bot/update.sh
  -> git fetch origin main
  -> git reset --hard origin/main
  -> git clean -fd
  -> uv sync --no-dev
  -> sudo systemctl restart bot.service
```

`git reset --hard origin/main` 和 `git clean -fd` 与现有“重载插件”行为一致，确保服务器不会因残留本地修改阻止部署。部署脚本不执行 `git push`，也不修改 GitHub 内容。

## 错误处理与安全

- workflow 使用 `script_stop: true`；SSH 脚本中任一步失败都会让 job 失败。
- `update.sh` 使用 `set -Eeuo pipefail`，未定义变量、命令失败或管道失败都会中止脚本。
- `uv sync --no-dev` 在重启前执行；依赖同步失败时保持旧进程运行。
- systemd 重启失败会使部署失败，并保留失败状态供 Actions 日志查看。
- 私钥只通过 GitHub Secrets 注入；不写入 workflow、脚本或 README。
- `bot.service` 需要允许 SSH 用户执行 `systemctl restart bot.service`。推荐在服务器上配置仅针对该服务的 sudoers 规则，而不是开放完整 sudo 权限。

## 验证

- 对 workflow 文件进行 YAML/结构检查，确认触发分支、SSH action、Secrets 和远端脚本路径正确。
- 对 `update.sh` 进行 shell 语法检查，并在不连接生产机的情况下验证关键命令、服务名和失败即退出行为。
- 运行现有 Python 测试，确认部署配置没有影响业务代码。
- 最终需要在 GitHub 仓库中配置 Secrets，并通过一次 `main` push 或手动重跑 workflow 验证真实 SSH 部署链路。

## 不包含的内容

- 不修改现有 QQ“重载插件”指令。
- 不新增公网 webhook 或 OneBot API 部署入口。
- 不把生产 `.env`、SSH 私钥或服务器具体凭据提交到仓库。
