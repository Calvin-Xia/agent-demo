"""Smoke tests for the ``chat`` CLI surface.

These tests deliberately avoid real network access: they cover argument
parsing, the presence of the ``chat`` subcommand, and the two failure paths
that happen *before* any HTTP request is attempted (missing API key and an
invalid ``--api`` value).

Real end-to-end calls against the DeepSeek API are verified manually and
recorded in ``docs/harness-verification.md``; they are not part of the
deterministic test suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from rs_agent.__main__ import build_parser, main

REPO_ROOT = Path(__file__).resolve().parents[1]

# The repository may contain a local ``.env`` with a working key. Tests that
# assert the "missing key" behaviour point at a path that cannot exist so they
# stay independent of whatever the developer keeps locally.
_MISSING_ENV_FILE = REPO_ROOT / ".env.does-not-exist"

# Likewise, a developer may have saved local profiles (``.rs_agent_profiles.json``
# is gitignored). The "missing key" cases disable profile reuse explicitly.
_MISSING_PROFILES_FILE = REPO_ROOT / ".profiles-does-not-exist.json"

# Any of these being set would make the "no key" cases depend on the host
# environment instead of the test itself.
_NO_KEY_ENV_VARS = ("DEEPSEEK_API_KEY", "RS_AGENT_API_KEY", "RS_AGENT_BASE_URL")


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    for name in _NO_KEY_ENV_VARS:
        env.pop(name, None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "rs_agent", *args],
        cwd=REPO_ROOT,
        env=_clean_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


# ---------------------------------------------------------------------------
# Parser-level
# ---------------------------------------------------------------------------


def test_chat_subcommand_exists_and_accepts_prompt() -> None:
    parser = build_parser()

    one_shot = parser.parse_args(["chat", "检查", "examples/sample.ppm"])
    assert one_shot.command == "chat"
    assert one_shot.prompt == ["检查", "examples/sample.ppm"]

    interactive = parser.parse_args(["chat"])
    assert interactive.command == "chat"
    assert interactive.prompt == []


def test_chat_parses_all_documented_options() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "chat",
            "--base-url",
            "https://example.invalid/v1",
            "--api-key",
            "test-key",
            "--model",
            "test-model",
            "--api",
            "responses",
            "--max-steps",
            "3",
            "--max-tokens",
            "64",
            "--temperature",
            "0.2",
            "--thinking",
            "enabled",
            "--reasoning-effort",
            "low",
            "--verbose",
            "prompt",
        ]
    )

    assert args.base_url == "https://example.invalid/v1"
    assert args.api_key == "test-key"
    assert args.model == "test-model"
    assert args.api == "responses"
    assert args.max_steps == 3
    assert args.max_tokens == 64
    assert args.temperature == pytest.approx(0.2)
    assert args.thinking == "enabled"
    assert args.reasoning_effort == "low"
    assert args.verbose is True
    assert args.prompt == ["prompt"]


def test_invalid_api_value_is_rejected_by_argparse(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = build_parser()

    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["chat", "--api", "grpc", "hi"])

    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert "invalid choice" in stderr
    assert "responses" in stderr


# ---------------------------------------------------------------------------
# In-process failure paths
# ---------------------------------------------------------------------------


def test_main_returns_one_when_api_key_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in _NO_KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    exit_code = main(
        [
            "chat",
            "--env-file",
            str(_MISSING_ENV_FILE),
            "--no-profile",
            "--profiles-file",
            str(_MISSING_PROFILES_FILE),
            "hello",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Missing API key" in captured.err
    assert "Traceback" not in captured.err


# ---------------------------------------------------------------------------
# Subprocess-level (proves the real console entry point behaves the same way)
# ---------------------------------------------------------------------------


def test_cli_chat_help_lists_harness_options() -> None:
    result = _run_cli("chat", "--help")

    assert result.returncode == 0
    assert "chat" in result.stdout
    assert "--max-steps" in result.stdout
    assert "responses" in result.stdout


def test_cli_without_api_key_exits_one_with_readable_error() -> None:
    result = _run_cli(
        "chat",
        "--env-file",
        str(_MISSING_ENV_FILE),
        "--no-profile",
        "--profiles-file",
        str(_MISSING_PROFILES_FILE),
        "hello",
    )

    assert result.returncode == 1
    assert "Missing API key" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_rejects_invalid_api_value() -> None:
    result = _run_cli("chat", "--api", "grpc", "hello")

    assert result.returncode == 2
    assert "invalid choice" in result.stderr
    assert "Traceback" not in result.stderr
