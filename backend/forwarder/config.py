"""Backend settings: Settings and load_settings."""

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = BACKEND_DIR.parent / ".env"


@dataclass
class Settings:
    llm_provider: str  # normal startup uses DeepSeek; test fixtures can inject a provider
    llm_fallback_reason: str | None  # retained for the health response schema
    deepseek_api_key: str | None = field(repr=False)  # never printed, logged or sent to the frontend
    deepseek_base_url: str = "https://api.deepseek.com"
    agent_model: str = "deepseek-flash"
    agent_thinking: bool = False
    agent_max_steps: int = 10
    auto_reply_readonly: bool = True
    db_path: Path = BACKEND_DIR / "var" / "forwarder.db"
    world_dir: Path = BACKEND_DIR / "data" / "world"
    company_display_name: str = "货代"
    sse_ping_seconds: float = 15.0


def _to_bool(value: str) -> bool:
    lowered = value.lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    raise ValueError("expected true or false")


# Settings fields read directly from the environment variable of the same name in upper case.
_CONVERTERS: dict[str, Callable[[str], Any]] = {
    "deepseek_base_url": str,
    "agent_model": str,
    "agent_thinking": _to_bool,
    "agent_max_steps": int,
    "auto_reply_readonly": _to_bool,
    "db_path": Path,
    "world_dir": Path,
    "company_display_name": str,
    "sse_ping_seconds": float,
}


def load_settings(env_file: Path | None = None, overrides: dict | None = None) -> Settings:
    """Build Settings from overrides > process environment > .env file > defaults.

    env_file defaults to the .env in the repository root. It is read with python-dotenv and
    never copied into os.environ. A blank value counts as unset: the field keeps its default
    and lower-priority sources are not consulted, so overrides={"DEEPSEEK_API_KEY": ""}
    forces "no key". Application startup requires a DeepSeek key.
    """
    file_values = dotenv_values(DEFAULT_ENV_FILE if env_file is None else env_file, encoding="utf-8")
    sources = (overrides or {}, os.environ, file_values)

    def lookup(name: str) -> str | None:
        for source in sources:
            value = source.get(name)
            if value is not None:
                return str(value).strip() or None
        return None

    values: dict[str, Any] = {}
    for field_name, convert in _CONVERTERS.items():
        name = field_name.upper()
        raw = lookup(name)
        if raw is None:
            continue
        try:
            values[field_name] = convert(raw)
        except ValueError as exc:
            raise ValueError(f"invalid {name}={raw!r}: {exc}") from None

    api_key = lookup("DEEPSEEK_API_KEY")
    if (lookup("LLM_PROVIDER") or "deepseek").lower() != "deepseek":
        raise ValueError("LLM_PROVIDER 只支持 deepseek，请更新项目的 .env。")
    if api_key is None:
        raise ValueError("请在项目根目录的 .env 中配置 DEEPSEEK_API_KEY 后启动。")
    return Settings(
        llm_provider="deepseek",
        llm_fallback_reason=None,
        deepseek_api_key=api_key,
        **values,
    )
