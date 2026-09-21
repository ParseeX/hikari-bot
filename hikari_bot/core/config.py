"""Application configuration loaded once during process startup.

Deployment-specific values belong in an ignored dotenv file or the process
environment.  Business constants should remain close to their feature code.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class ConfigurationError(RuntimeError):
    """Raised when an enabled feature is missing required configuration."""


def _resolve_env_file() -> Path | None:
    """Find the dotenv file without overriding real process environment values."""
    explicit = os.getenv("HIKARI_ENV_FILE", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.is_file():
            raise ConfigurationError(f"HIKARI_ENV_FILE does not exist: {path}")
        return path.resolve()

    profile = os.getenv("HIKARI_ENV", "").strip()
    candidates = []
    if profile:
        candidates.append(PROJECT_ROOT / f".env.{profile}")
    # Keep the existing server convention working without changing its command.
    candidates.extend((PROJECT_ROOT / ".env.prod", PROJECT_ROOT / ".env"))
    return next((path.resolve() for path in candidates if path.is_file()), None)


ENV_FILE = _resolve_env_file()
if ENV_FILE:
    load_dotenv(ENV_FILE, override=False, encoding="utf-8")


def _parse_string_set(name: str, default: tuple[str, ...] = ()) -> frozenset[str]:
    raw = os.getenv(name)
    if raw is None:
        return frozenset(default)

    raw = raw.strip()
    if not raw:
        return frozenset()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = [part.strip() for part in raw.split(",")]

    if isinstance(parsed, str):
        parsed = [parsed]
    if not isinstance(parsed, (list, tuple, set)):
        raise ConfigurationError(f"{name} must be a JSON array or comma-separated list")
    return frozenset(str(item).strip() for item in parsed if str(item).strip() or item == "")


def _optional_path(name: str) -> Path | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _positive_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


def _path(name: str, default: Path) -> Path:
    raw = os.getenv(name, "").strip()
    path = Path(raw).expanduser() if raw else default
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


T = TypeVar("T")


@dataclass(frozen=True)
class Settings:
    env_file: Path | None = ENV_FILE
    superusers: frozenset[str] = field(
        default_factory=lambda: _parse_string_set("SUPERUSERS")
    )
    api_timeout: float = field(
        default_factory=lambda: _positive_float("API_TIMEOUT", 120.0)
    )

    uptime_token: str = field(
        default_factory=lambda: os.getenv("UPTIME_TOKEN", "").strip(),
        repr=False,
    )
    jihuanshe_token: str = field(
        default_factory=lambda: os.getenv("JIHUANSHE_TOKEN", "").strip(),
        repr=False,
    )
    jihuanshe_bridge_url: str = field(
        default_factory=lambda: os.getenv("JIHUANSHE_BRIDGE_URL", "").strip(),
    )
    jihuanshe_bridge_token: str = field(
        default_factory=lambda: os.getenv("JIHUANSHE_BRIDGE_TOKEN", "").strip(),
        repr=False,
    )
    jihuanshe_bridge_timeout: float = field(
        default_factory=lambda: _positive_float("JIHUANSHE_BRIDGE_TIMEOUT", 90.0)
    )
    jm_pdf_password: str = field(
        default_factory=lambda: os.getenv("JM_PDF_PASSWORD", "").strip(),
        repr=False,
    )
    nas_power_url: str = field(
        default_factory=lambda: os.getenv("NAS_POWER_URL", "").strip(),
    )
    nas_power_token: str = field(
        default_factory=lambda: os.getenv("NAS_POWER_TOKEN", "").strip(),
        repr=False,
    )
    nas_power_timeout: float = field(
        default_factory=lambda: _positive_float("NAS_POWER_TIMEOUT", 15.0)
    )
    ruling_assistant_api_url: str = field(
        default_factory=lambda: os.getenv(
            "RULING_ASSISTANT_API_URL",
            "https://ocg-ruling-assistant.vercel.app/api/answer",
        ).strip(),
    )
    ruling_assistant_timeout: float = field(
        default_factory=lambda: _positive_float("RULING_ASSISTANT_TIMEOUT", 310.0)
    )
    local_llm_api_base_url: str = field(
        default_factory=lambda: os.getenv(
            "LOCAL_LLM_API_BASE_URL", "http://192.168.0.100:1234/v1"
        ).strip().rstrip("/"),
    )
    local_llm_model: str = field(
        default_factory=lambda: os.getenv(
            "LOCAL_LLM_MODEL", "qwen/qwen3.5-9b"
        ).strip(),
    )
    local_llm_api_key: str = field(
        default_factory=lambda: os.getenv("LOCAL_LLM_API_KEY", "").strip(),
        repr=False,
    )
    local_llm_timeout: float = field(
        default_factory=lambda: _positive_float("LOCAL_LLM_TIMEOUT", 180.0)
    )
    local_llm_image_max_count: int = field(
        default_factory=lambda: _positive_int("LOCAL_LLM_IMAGE_MAX_COUNT", 4)
    )
    local_llm_image_max_bytes: int = field(
        default_factory=lambda: _positive_int("LOCAL_LLM_IMAGE_MAX_BYTES", 10 * 1024 * 1024)
    )
    local_llm_system_prompt: str = field(
        default_factory=lambda: os.getenv("LOCAL_LLM_SYSTEM_PROMPT", "").strip(),
    )

    cardrush_proxy_url: str | None = field(
        default_factory=lambda: os.getenv("CARDRUSH_PROXY_URL", "").strip() or None
    )
    public_group_id: str | None = field(
        default_factory=lambda: os.getenv("PUBLIC_GROUP_ID", "").strip() or None
    )
    jm_data_dir: Path = field(
        default_factory=lambda: _path("JM_DATA_DIR", PACKAGE_ROOT / "data" / "jm")
    )

    def require(self, name: str, value: T | None) -> T:
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ConfigurationError(f"{name} is not configured")
        return value

    @property
    def cardrush_proxies(self) -> dict[str, str] | None:
        if not self.cardrush_proxy_url:
            return None
        return {
            "http": self.cardrush_proxy_url,
            "https": self.cardrush_proxy_url,
        }


settings = Settings()
