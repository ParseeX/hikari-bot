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
