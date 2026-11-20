import os
from dataclasses import fields
from pathlib import Path

import pytest

from forwarder.config import Settings, load_settings


@pytest.fixture(autouse=True)
def no_settings_in_environment(monkeypatch):
    # Keep variables set in the developer's shell out of these tests.
    for f in fields(Settings):
        monkeypatch.delenv(f.name.upper(), raising=False)


def test_missing_key_stops_startup(tmp_path):
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        load_settings(env_file=tmp_path / "missing.env")


def test_deepseek_selected_when_key_present(tmp_path):
    s = load_settings(env_file=tmp_path / "x.env", overrides={"DEEPSEEK_API_KEY": "sk-test"})
    assert s.llm_provider == "deepseek"
    assert s.agent_model == "deepseek-flash" and s.agent_thinking is False


def test_mock_provider_is_not_available_at_startup(tmp_path):
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        load_settings(env_file=tmp_path / "x.env", overrides={"LLM_PROVIDER": "mock", "DEEPSEEK_API_KEY": "sk-test"})


def test_precedence_is_overrides_then_environment_then_env_file_then_defaults(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AGENT_MODEL=file-model\n"
        "DEEPSEEK_API_KEY=sk-from-file\n"
        "AGENT_MAX_STEPS=7\n"
        "AGENT_THINKING=true\n"
        "SSE_PING_SECONDS=2.5\n"
        "COMPANY_DISPLAY_NAME=한빛（演示）\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_MODEL", "env-model")
    monkeypatch.setenv("AGENT_MAX_STEPS", "8")

    s = load_settings(env_file=env_file, overrides={"AGENT_MODEL": "override-model"})

    assert s.agent_model == "override-model"
    assert s.agent_max_steps == 8
    assert s.agent_thinking is True and s.sse_ping_seconds == 2.5
    assert s.company_display_name == "한빛（演示）"
    assert s.deepseek_base_url == "https://api.deepseek.com" and s.auto_reply_readonly is True
    assert "AGENT_THINKING" not in os.environ  # the .env file is read, never copied into os.environ


def test_blank_override_hides_key_set_in_environment_and_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=sk-from-file\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        load_settings(env_file=env_file, overrides={"DEEPSEEK_API_KEY": ""})


def test_default_paths_are_under_backend(tmp_path):
    s = load_settings(env_file=tmp_path / "x.env", overrides={"DEEPSEEK_API_KEY": "sk-test"})
    backend_dir = Path(__file__).resolve().parents[1]
    assert s.db_path == backend_dir / "var" / "forwarder.db"
    assert s.world_dir == backend_dir / "data" / "world"


def test_unknown_provider_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        load_settings(env_file=tmp_path / "x.env", overrides={"LLM_PROVIDER": "unknown", "DEEPSEEK_API_KEY": "sk-test"})


def test_invalid_boolean_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="AGENT_THINKING"):
        load_settings(env_file=tmp_path / "x.env", overrides={"AGENT_THINKING": "maybe"})


def test_api_key_is_kept_out_of_repr(tmp_path):
    s = load_settings(env_file=tmp_path / "x.env", overrides={"DEEPSEEK_API_KEY": "sk-secret"})
    assert s.deepseek_api_key == "sk-secret" and "sk-secret" not in repr(s)
