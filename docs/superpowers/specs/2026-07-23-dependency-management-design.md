# 依赖与 Python 版本约束设计

## 目标

让项目能够从干净环境中通过一条命令安装，并让开发机、CI 和生产服务器解析到相同的依赖版本。

## 方案选择

采用 `pyproject.toml + uv.lock`：

- `pyproject.toml` 是依赖声明的唯一来源，记录项目支持的版本范围。
- `uv.lock` 由 uv 生成并提交到 Git，记录直接依赖和间接依赖的精确解析结果。
- 使用 `uv sync` 创建或同步虚拟环境，使用 `uv run python bot.py` 启动项目。
- 不维护第二份手写 `requirements.txt`，避免两个依赖清单发生漂移。

项目当前直接从仓库根目录运行，不在本次改造成可发布的 Python 包。因此在 `[tool.uv]` 中设置 `package = false`，uv 只管理解释器、虚拟环境和依赖，不尝试构建或安装 `hikari-bot` 本身。

## Python 版本

将 `requires-python` 从 `>=3.9, <4.0` 改为：

```toml
requires-python = ">=3.10, <3.14"
```

最低版本设为 3.10，因为源码使用了 `dict | None`、`list[str]` 等语法。最高版本限制在 3.14 之前，避免对尚未纳入本次验证范围的解释器版本作兼容承诺。

锁文件必须覆盖整个受支持的 Python 区间，而不是只针对当前开发机的 Python 3.12。

## 依赖分组

### 运行时依赖

`[project].dependencies` 只声明机器人、Web 路由、监控任务和资源生成代码直接需要的包：

- NoneBot 核心、FastAPI 驱动和 OneBot V11 适配器
- `nonebot-plugin-apscheduler`、`nonebot-plugin-parser`、`nonebot-plugin-easy-translate`
- `python-dotenv`
- `aiohttp`、`httpx`、`requests[socks]`
- `fastapi`、`pydantic`、`python-multipart`、`jinja2`
- `beautifulsoup4`
- `pillow`、`matplotlib`、`pytz`
- `playwright`
- `cairosvg`、`pymupdf`、`fonttools`
- `jmcomic`

直接导入的包即使也是其他包的间接依赖，仍显式声明，避免上游移除间接依赖后项目失效。版本使用兼容范围约束；最终精确版本交给 `uv.lock`。

### 可选脚本依赖

`scripts/bili_login.py` 不参与机器人正常启动，其依赖放入 `bili-login` 可选依赖组：

- `bilibili-api-python`
- `qrcode-terminal`

需要运行该脚本时使用：

```powershell
uv sync --extra bili-login
uv run python scripts/bili_login.py
```

### 开发依赖

静态检查和测试工具放入 `dependency-groups.dev`：

- `pyflakes`
- `pytest`

默认 `uv sync` 安装开发组，生产部署可使用 `uv sync --no-dev`。

## 非 Python 依赖

Playwright 的 Chromium 浏览器文件不属于 Python wheel，锁文件不能代替浏览器安装。README 必须明确首次部署执行：

```powershell
uv run playwright install chromium
```

系统字体、Cairo 运行库和其他操作系统级依赖不写入 Python 依赖列表；若 uv 安装或导入验证暴露缺失项，应在 README 中单独记录，不用虚假的 PyPI 包替代。

## 锁定与更新流程

首次生成：

```powershell
uv lock
uv sync
```

日常验证：

```powershell
uv lock --check
```

升级单个依赖：

```powershell
uv lock --upgrade-package <package-name>
```

修改依赖时必须同时提交 `pyproject.toml` 和 `uv.lock`。

## 验证标准

本次改动满足以下条件才算完成：

- `uv lock` 能在无冲突的情况下生成锁文件。
- `uv lock --check` 通过。
- `uv sync` 能建立完整开发环境。
- 所有项目 Python 文件通过 `compileall`。
- `pyflakes` 不报告未定义名称。
- `pytest` 执行成功；当前没有测试时允许报告 `no tests collected`，但不得出现测试导入错误。
- 在 uv 环境中对项目全部第三方顶层模块执行导入冒烟检查。
- README 包含安装、启动、可选 B 站登录依赖及 Playwright 浏览器安装命令。

## 不在本次范围内的事项

- 不调整业务代码、插件结构或服务接口。
- 不将项目发布到 PyPI。
- 不新增 Docker、CI 或生产部署脚本。
- 不替换现有资源文件和操作系统级依赖。
- 不升级或重构第一、第二项优化已经完成的配置与导入代码。
