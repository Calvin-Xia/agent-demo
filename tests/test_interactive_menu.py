"""Tests for the interactive configuration menu and .env wiring."""

import io
import sys

import pytest

from rs_agent.__main__ import (
    _build_config,
    _interactive_menu,
    build_parser,
)
from rs_agent.llm import LLMConfig


def _run_menu(monkeypatch, replies: list[str]) -> tuple[dict, str]:
    stdin = io.StringIO("".join(reply + "\n" for reply in replies))
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stderr", stderr)
    config = LLMConfig(
        api_key="k",
        api="chat",
        model="deepseek-flash",
        thinking="disabled",
    )
    selection = _interactive_menu(config)
    return selection, stderr.getvalue()


def test_menu_defaults_on_empty_answers(monkeypatch):
    selection, _stderr = _run_menu(monkeypatch, ["", "", ""])

    assert selection == {
        "profile": None,
        "api": "chat",
        "model": "deepseek-flash",
        "thinking": "disabled",
        "reasoning_effort": None,
    }


def test_menu_selects_responses_model_and_high_effort(monkeypatch):
    selection, stderr = _run_menu(monkeypatch, ["2", "2", "3"])

    assert selection == {
        "profile": None,
        "api": "responses",
        "model": "deepseek-v4-pro",
        "thinking": "enabled",
        "reasoning_effort": "high",
    }
    assert "① 接口协议" in stderr
    assert "② 模型" in stderr
    assert "③ 思考强度" in stderr


def test_menu_accepts_custom_model_id(monkeypatch):
    selection, _stderr = _run_menu(monkeypatch, ["1", "3", "my-custom-model", "1"])

    assert selection["api"] == "chat"
    assert selection["model"] == "my-custom-model"
    assert selection["thinking"] == "disabled"
    assert selection["reasoning_effort"] is None


def test_menu_retries_invalid_choice(monkeypatch):
    selection, stderr = _run_menu(monkeypatch, ["9", "1", "1", "1"])

    assert selection["api"] == "chat"
    assert "输入无效" in stderr


def test_menu_eof_keeps_defaults(monkeypatch):
    stdin = io.StringIO("")
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    config = LLMConfig(api_key="k", api="chat", model="deepseek-flash")

    selection = _interactive_menu(config)

    assert selection == {
        "profile": None,
        "api": "chat",
        "model": "deepseek-flash",
        "thinking": "disabled",
        "reasoning_effort": None,
    }


def test_parser_exposes_env_file_and_no_menu():
    args = build_parser().parse_args(["chat", "--env-file", "custom.env", "--no-menu"])

    assert args.env_file == "custom.env"
    assert args.no_menu is True

    defaults = build_parser().parse_args(["chat"])
    assert defaults.env_file == ".env"
    assert defaults.no_menu is False


def test_build_config_reads_env_file(tmp_path, monkeypatch):
    for name in ("DEEPSEEK_API_KEY", "RS_AGENT_API_KEY", "RS_AGENT_MODEL"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEEPSEEK_API_KEY=key-from-dotenv\n"
        "RS_AGENT_MODEL=model-from-dotenv\n"
        "RS_AGENT_THINKING=enabled\n"
        "RS_AGENT_REASONING_EFFORT=low\n",
        encoding="utf-8",
    )
    args = build_parser().parse_args(["chat", "--env-file", str(env_file), "hi"])

    config = _build_config(args)

    assert config.api_key == "key-from-dotenv"
    assert config.model == "model-from-dotenv"
    assert config.thinking == "enabled"
    assert config.reasoning_effort == "low"


def test_menu_defaults_reflect_saved_thinking(monkeypatch):
    stdin = io.StringIO("\n\n\n")
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    config = LLMConfig(
        api_key="k",
        api="responses",
        model="deepseek-v4-pro",
        thinking="enabled",
        reasoning_effort="low",
    )

    selection = _interactive_menu(config)

    assert selection == {
        "profile": None,
        "api": "responses",
        "model": "deepseek-v4-pro",
        "thinking": "enabled",
        "reasoning_effort": "low",
    }


def test_build_config_rejects_invalid_api_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("RS_AGENT_API", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("RS_AGENT_API=grpc\n", encoding="utf-8")
    args = build_parser().parse_args(["chat", "--env-file", str(env_file), "hi"])

    with pytest.raises(ValueError, match="Unsupported api"):
        _build_config(args)
