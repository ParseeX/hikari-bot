# Dependency Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Declare every direct Python dependency with compatible version bounds, adopt Python 3.10–3.13 as the supported range, and commit a reproducible universal `uv.lock`.

**Architecture:** Keep `pyproject.toml` as the only hand-edited dependency source and configure uv as a non-package project. Separate bot runtime dependencies, the optional Bilibili login script, and development checks; let `uv.lock` pin the complete transitive graph.

**Tech Stack:** Python 3.10–3.13, PEP 621 `pyproject.toml`, uv, pytest, pyflakes

## Global Constraints

- Set `requires-python = ">=3.10, <3.14"`.
- Keep `pyproject.toml` as the only hand-edited dependency declaration; do not add `requirements.txt`.
- Configure `[tool.uv] package = false`; do not turn the repository into a publishable package.
- Put `bilibili-api-python` and `qrcode-terminal` in the `bili-login` optional extra.
- Put `pytest`, `pyflakes`, and the Python 3.10 TOML fallback in `dependency-groups.dev`.
- Do not change business code, plugin registration, service interfaces, deployment scripts, or resource files.
- Document that Playwright Chromium requires a separate `uv run playwright install chromium`.

---

### Task 1: Declare and test project metadata

**Files:**
- Create: `tests/test_project_metadata.py`
- Modify: `pyproject.toml:1-16`

**Interfaces:**
- Consumes: the existing PEP 621 `[project]` and `[tool.nonebot]` tables.
- Produces: a Python 3.10-compatible project declaration with runtime dependencies, `bili-login`, the `dev` group, and non-package uv configuration.

- [ ] **Step 1: Add the failing metadata regression test**

```python
import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _dependency_name(specifier: str) -> str:
    return re.split(r"[\s\[<>=!~;]", specifier, maxsplit=1)[0].lower()


def _load_pyproject() -> dict:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)


def test_supported_python_range_matches_source_syntax():
    project = _load_pyproject()["project"]
    assert project["requires-python"] == ">=3.10, <3.14"


def test_runtime_and_optional_dependencies_are_declared():
    metadata = _load_pyproject()
    runtime = {
        _dependency_name(specifier)
        for specifier in metadata["project"]["dependencies"]
    }
    assert {
        "nonebot2",
        "nonebot-adapter-onebot",
        "nonebot-plugin-apscheduler",
        "nonebot-plugin-parser",
        "nonebot-plugin-easy-translate",
        "python-dotenv",
        "aiohttp",
        "httpx",
        "requests",
        "fastapi",
        "pydantic",
        "python-multipart",
        "jinja2",
        "beautifulsoup4",
        "pillow",
        "matplotlib",
        "pytz",
        "playwright",
        "cairosvg",
        "pymupdf",
        "fonttools",
        "jmcomic",
    } <= runtime

    optional = metadata["project"]["optional-dependencies"]
    assert {
        _dependency_name(specifier)
        for specifier in optional["bili-login"]
    } == {"bilibili-api-python", "qrcode-terminal"}


def test_uv_and_development_groups_are_configured():
    metadata = _load_pyproject()
    assert metadata["tool"]["uv"]["package"] is False

    development = {
        _dependency_name(specifier)
        for specifier in metadata["dependency-groups"]["dev"]
    }
    assert {"pyflakes", "pytest", "tomli"} <= development
```

- [ ] **Step 2: Run the metadata test and verify the old declaration fails**

Run:

```powershell
python -m pytest tests/test_project_metadata.py -q
```

Expected: at least `test_supported_python_range_matches_source_syntax` fails because the current value is `>=3.9, <4.0`.

- [ ] **Step 3: Replace `pyproject.toml` with the complete dependency declaration**

```toml
[project]
name = "hikari-bot"
version = "0.1.0"
description = "hikari-bot"
readme = "README.md"
requires-python = ">=3.10, <3.14"
dependencies = [
    "nonebot2[fastapi]>=2.5.0,<3.0.0",
    "nonebot-adapter-onebot>=2.4.6,<3.0.0",
    "nonebot-plugin-apscheduler>=0.5.0,<1.0.0",
    "nonebot-plugin-parser>=2.6.6,<3.0.0",
    "nonebot-plugin-easy-translate>=0.2.4,<1.0.0",
    "python-dotenv>=1.0.0,<2.0.0",
    "aiohttp>=3.10.0,<4.0.0",
    "httpx>=0.27.0,<1.0.0",
    "requests[socks]>=2.32.0,<3.0.0",
    "fastapi>=0.115.0,<1.0.0",
    "pydantic>=2.8.0,<3.0.0",
    "python-multipart>=0.0.9,<1.0.0",
    "jinja2>=3.1.0,<4.0.0",
    "beautifulsoup4>=4.12.0,<5.0.0",
    "pillow>=10.4.0,<13.0.0",
    "matplotlib>=3.9.0,<4.0.0",
    "pytz>=2024.1",
    "playwright>=1.46.0,<2.0.0",
    "cairosvg>=2.7.0,<3.0.0",
    "pymupdf>=1.24.0,<2.0.0",
    "fonttools>=4.53.0,<5.0.0",
    "jmcomic>=2.7.2,<3.0.0",
]

[project.optional-dependencies]
bili-login = [
    "bilibili-api-python>=17.4.2,<18.0.0",
    "qrcode-terminal>=0.8,<1.0",
]

[dependency-groups]
dev = [
    "pyflakes>=3.2.0,<4.0.0",
    "pytest>=8.3.0,<9.0.0",
    "tomli>=2.0.0,<3.0.0; python_version < '3.11'",
]

[tool.uv]
package = false

[tool.nonebot]
adapters = [
    {name = "OneBot V11", module_name = "nonebot.adapters.onebot.v11"}
]
plugins = ["nonebot_plugin_parser", "nonebot_plugin_easy_translate", "nonebot_plugin_apscheduler"]
plugin_dirs = ["hikari_bot/plugins"]
builtin_plugins = ["echo"]
```

- [ ] **Step 4: Run the metadata test and verify it passes**

Run:

```powershell
python -m pytest tests/test_project_metadata.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Check formatting before dependency resolution**

Run:

```powershell
git diff --check
```

Expected: exit code `0` with no whitespace errors.

### Task 2: Resolve and lock the dependency graph

**Files:**
- Create: `uv.lock`
- Modify: `pyproject.toml` only if uv reports a real compatibility conflict
- Test: `tests/test_project_metadata.py`

**Interfaces:**
- Consumes: all compatible ranges declared by Task 1.
- Produces: a universal `uv.lock` accepted by uv for Python `>=3.10,<3.14`.

- [ ] **Step 1: Install the uv command in the current development environment**

Run:

```powershell
python -m pip install uv
```

Expected: `uv --version` subsequently prints an installed version.

- [ ] **Step 2: Generate the universal lock file**

Run:

```powershell
uv lock
```

Expected: uv resolves the runtime, optional, and development dependency graph and creates `uv.lock`.

If resolution reports an incompatibility, stop this task and preserve the exact resolver output for review. Do not silently remove a directly imported dependency or change the approved Python support range.

- [ ] **Step 3: Verify that the lock matches project metadata**

Run:

```powershell
uv lock --check
```

Expected: exit code `0`.

- [ ] **Step 4: Synchronize the complete development environment**

Run:

```powershell
uv sync --all-extras
```

Expected: `.venv` is created or updated with the development group, runtime dependencies, and the `bili-login` extra.

- [ ] **Step 5: Run the metadata test inside the locked environment**

Run:

```powershell
uv run pytest tests/test_project_metadata.py -q
```

Expected: `3 passed`.

- [ ] **Step 6: Commit the dependency declaration, regression test, and lock**

```powershell
git add pyproject.toml uv.lock tests/test_project_metadata.py
git commit -m "build: lock project dependencies with uv"
```

### Task 3: Document installation and operation

**Files:**
- Modify: `README.md:1-4`

**Interfaces:**
- Consumes: the commands and dependency groups created by Tasks 1–2.
- Produces: copyable setup, startup, production, optional script, and lock-validation commands.

- [ ] **Step 1: Add installation instructions after the repository title**

Insert the following section before `## 生产环境配置`:

````markdown
## 安装与启动

项目支持 Python 3.10～3.13，并使用 [uv](https://docs.astral.sh/uv/) 管理依赖。

安装开发环境：

```powershell
uv sync
uv run playwright install chromium
```

启动机器人：

```powershell
uv run python bot.py
```

生产环境不安装开发工具：

```powershell
uv sync --no-dev
uv run playwright install chromium
uv run python bot.py
```

运行 B 站扫码登录脚本时，额外安装可选依赖：

```powershell
uv sync --extra bili-login
uv run python scripts/bili_login.py
```

修改 `pyproject.toml` 中的依赖后运行 `uv lock`，并同时提交
`pyproject.toml` 与 `uv.lock`。可用以下命令检查锁文件是否为最新：

```powershell
uv lock --check
```

Playwright 的 Chromium 浏览器不包含在 Python 锁文件中，因此每个新环境都需要单独执行一次
`uv run playwright install chromium`。
````

- [ ] **Step 2: Verify README commands and version range**

Run:

```powershell
rg -n "Python 3\\.10～3\\.13|uv sync|uv run python bot\\.py|playwright install chromium|bili-login|uv lock --check" README.md
```

Expected: every installation, startup, browser, optional-extra, and lock-validation command appears in the new section.

- [ ] **Step 3: Commit the documentation**

```powershell
git add README.md
git commit -m "docs: document uv setup and startup"
```

### Task 4: Verify the locked project end to end

**Files:**
- Verify: `pyproject.toml`
- Verify: `uv.lock`
- Verify: `README.md`
- Verify: `tests/test_project_metadata.py`

**Interfaces:**
- Consumes: the complete dependency declaration, universal lock, synchronized `.venv`, and documentation.
- Produces: evidence that metadata, imports, source compilation, and static checks succeed in the locked environment.

- [ ] **Step 1: Verify the lock is current**

Run:

```powershell
uv lock --check
```

Expected: exit code `0`.

- [ ] **Step 2: Verify all declared third-party modules import**

Run:

```powershell
@'
import importlib

modules = [
    "nonebot",
    "nonebot.adapters.onebot.v11",
    "nonebot_plugin_apscheduler",
    "nonebot_plugin_parser",
    "nonebot_plugin_easy_translate",
    "dotenv",
    "aiohttp",
    "httpx",
    "requests",
    "fastapi",
    "pydantic",
    "multipart",
    "jinja2",
    "bs4",
    "PIL",
    "matplotlib",
    "pytz",
    "playwright.async_api",
    "cairosvg",
    "fitz",
    "fontTools",
    "jmcomic",
    "bilibili_api",
    "qrcode_terminal",
]

for module in modules:
    importlib.import_module(module)

print(f"imported {len(modules)} third-party modules")
'@ | uv run python -
```

Expected: `imported 24 third-party modules`.

- [ ] **Step 3: Compile all repository Python sources**

Run:

```powershell
uv run python -m compileall -q bot.py hikari_bot scripts tests
```

Expected: exit code `0`.

- [ ] **Step 4: Run static undefined-name checks**

Run:

```powershell
uv run pyflakes bot.py hikari_bot scripts tests
```

Expected: no undefined-name diagnostics and exit code `0`.

- [ ] **Step 5: Run the complete test suite**

Run:

```powershell
uv run pytest -q
```

Expected: `3 passed`.

- [ ] **Step 6: Inspect the final worktree**

Run:

```powershell
git status --short
git log -3 --oneline
```

Expected: no uncommitted implementation files; the dependency and documentation commits are the latest commits.
