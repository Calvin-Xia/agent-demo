"""Tests for the interactive slash commands and profile-aware startup."""

import io
import sys

import pytest

from rs_agent.__main__ import ChatState, _handle_slash, build_parser, main
from rs_agent.harness import Harness
from rs_agent.llm import LLMClient, LLMConfig
from rs_agent.profiles import Profile, ProfileStore


def _parse(tmp_path, *extra: str):
    return build_parser().parse_args(
        [
            "chat",
            "--env-file",
            str(tmp_path / "missing.env"),
            "--profiles-file",
            str(tmp_path / "profiles.json"),
            *extra,
        ]
    )


def _state(tmp_path, config: LLMConfig | None = None) -> ChatState:
    args = _parse(tmp_path)
    store = ProfileStore(tmp_path / "profiles.json")
    config = config or LLMConfig(
        base_url="https://api.deepseek.com",
        api_key="sk-secret-value",
        model="deepseek-flash",
        api="chat",
        thinking="disabled",
    )
    harness = Harness(LLMClient(config), stdout=io.StringIO(), stderr=io.StringIO())
    return ChatState(
        args=args,
        store=store,
        profile_name=None,
        config=config,
        harness=harness,
    )


def test_parser_exposes_profile_options():
    args = build_parser().parse_args(
        ["chat", "--profile", "work", "--profiles-file", "p.json"]
    )
    assert args.profile == "work"
    assert args.profiles_file == "p.json"
    assert args.no_profile is False

    defaults = build_parser().parse_args(["chat"])
    assert defaults.profile is None
    assert defaults.no_profile is False


def test_save_creates_profile_without_literal_key(tmp_path, monkeypatch):
    monkeypatch.delenv("RS_AGENT_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    state = _state(tmp_path)

    assert _handle_slash("/save local", state) is True

    saved = state.store.get("local")
    assert saved is not None
    assert saved.model == "deepseek-flash"
    assert saved.api == "chat"
    assert saved.base_url == "https://api.deepseek.com"
    assert saved.api_key is None
    assert "sk-secret-value" not in (tmp_path / "profiles.json").read_text(
        encoding="utf-8"
    )
    assert state.store.last_profile == "local"


def test_save_accepts_explicit_key_env(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    state = _state(tmp_path)

    _handle_slash("/save proxy PROXY_API_KEY", state)

    saved = state.store.get("proxy")
    assert saved is not None
    assert saved.api_key_env == "PROXY_API_KEY"


def test_profiles_lists_saved_entries_with_active_marker(tmp_path, monkeypatch):
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    state = _state(tmp_path)
    state.store.upsert(Profile(name="work", model="deepseek-v4-pro"))
    state.profile_name = "work"

    _handle_slash("/profiles", state)

    output = stderr.getvalue()
    assert "work" in output
    assert "*" in output


def test_use_switches_profile_and_keeps_history(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    monkeypatch.setenv("REMOTE_KEY", "sk-remote-value")
    state = _state(tmp_path)
    state.store.upsert(
        Profile(
            name="remote",
            model="remote-model",
            api="responses",
            api_key_env="REMOTE_KEY",
        )
    )
    state.harness.messages.append({"role": "user", "content": "earlier turn"})

    _handle_slash("/use remote", state)

    assert state.profile_name == "remote"
    assert state.config.model == "remote-model"
    assert state.config.api == "responses"
    assert state.config.api_key == "sk-remote-value"
    assert state.store.last_profile == "remote"
    assert any(
        message.get("content") == "earlier turn"
        for message in state.harness.messages
    )


def test_use_missing_profile_reports_error(tmp_path, monkeypatch):
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    state = _state(tmp_path)

    assert _handle_slash("/use nope", state) is True

    assert "档案不存在" in stderr.getvalue()
    assert state.profile_name is None


def test_config_output_masks_key(tmp_path, monkeypatch):
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    state = _state(tmp_path)

    _handle_slash("/config", state)

    output = stderr.getvalue()
    assert "sk-****" in output
    assert "sk-secret-value" not in output
    assert "profile=(none)" in output


def test_delete_removes_profile_and_clears_active(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    state = _state(tmp_path)
    state.store.upsert(Profile(name="temp"))
    state.profile_name = "temp"

    _handle_slash("/delete temp", state)

    assert state.store.get("temp") is None
    assert state.profile_name is None


def test_help_unknown_command_and_exit(tmp_path, monkeypatch):
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stderr)
    state = _state(tmp_path)

    assert _handle_slash("/help", state) is True
    assert "/use <name>" in stderr.getvalue()

    monkeypatch.setattr(sys, "stderr", stderr := io.StringIO())
    assert _handle_slash("/whatever", state) is True
    assert "未知命令" in stderr.getvalue()

    assert _handle_slash("/exit", state) is False


def _write_store(tmp_path, profile: Profile) -> str:
    path = tmp_path / "profiles.json"
    store = ProfileStore(path)
    store.upsert(profile)
    store.set_last(profile.name)
    return str(path)


def _write_env(tmp_path) -> str:
    path = tmp_path / ".env"
    path.write_text("DEEPSEEK_API_KEY=sk-test-key\n", encoding="utf-8")
    return str(path)


def test_run_chat_reuses_last_profile_without_menu(
    tmp_path, monkeypatch, capsys
):
    profile = Profile(
        name="local",
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        api="chat",
        thinking="disabled",
        api_key_env="DEEPSEEK_API_KEY",
    )
    profiles_file = _write_store(tmp_path, profile)
    env_file = _write_env(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    exit_code = main(
        [
            "chat",
            "--no-menu",
            "--profiles-file",
            profiles_file,
            "--env-file",
            env_file,
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "profile=local" in captured.err
    assert "sk-test-key" not in captured.err
    assert "sk-****" in captured.err


def test_run_chat_rejects_unknown_profile(tmp_path, monkeypatch, capsys):
    profiles_file = _write_store(tmp_path, Profile(name="known"))
    env_file = _write_env(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))

    exit_code = main(
        [
            "chat",
            "--no-menu",
            "--profile",
            "missing",
            "--profiles-file",
            profiles_file,
            "--env-file",
            env_file,
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "档案不存在" in captured.err
    assert "Traceback" not in captured.err


def test_profile_and_no_profile_are_mutually_exclusive():
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["chat", "--profile", "x", "--no-profile"])

    assert excinfo.value.code == 2


def test_no_profile_ignores_saved_profile_in_menu(
    tmp_path, monkeypatch, capsys
):
    profile = Profile(
        name="localchat",
        model="deepseek-flash",
        api="chat",
        api_key_env="DEEPSEEK_API_KEY",
    )
    profiles_file = _write_store(tmp_path, profile)
    env_file = _write_env(tmp_path)
    # Menu answers: protocol, model, thinking (no profile step expected).
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n\n\n"))

    exit_code = main(
        [
            "chat",
            "--no-profile",
            "--profiles-file",
            profiles_file,
            "--env-file",
            env_file,
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "① 配置档案" not in captured.err
    assert "profile=(none)" in captured.err
    # The saved last profile must not be cleared by an ignored-profile run.
    assert ProfileStore(profiles_file).last_profile == "localchat"


def test_no_profile_ignores_menu_profile_selection(
    tmp_path, monkeypatch, capsys
):
    profiles_file = _write_store(tmp_path, Profile(name="localchat"))
    env_file = _write_env(tmp_path)
    # Even if a numbered answer would match a profile, no profile step exists.
    monkeypatch.setattr(sys, "stdin", io.StringIO("2\n1\n1\n1\n"))

    exit_code = main(
        [
            "chat",
            "--no-profile",
            "--profiles-file",
            profiles_file,
            "--env-file",
            env_file,
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "profile=(none)" in captured.err
    assert ProfileStore(profiles_file).last_profile == "localchat"
