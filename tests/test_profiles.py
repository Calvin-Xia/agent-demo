"""Tests for persistent named configuration profiles."""

import json

import pytest

from rs_agent.llm import LLMConfig
from rs_agent.profiles import Profile, ProfileError, ProfileStore


def test_missing_store_file_is_empty(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")

    assert store.names() == []
    assert store.last_profile is None
    assert store.get("anything") is None


def test_upsert_get_delete_and_last_profile_persist(tmp_path):
    path = tmp_path / "profiles.json"
    store = ProfileStore(path)
    profile = Profile(
        name="local",
        base_url="http://127.0.0.1:8765",
        model="my-model",
        api="chat",
        thinking="disabled",
        api_key_env="DEEPSEEK_API_KEY",
    )

    store.upsert(profile)
    store.set_last("local")

    reloaded = ProfileStore(path)
    assert reloaded.names() == ["local"]
    assert reloaded.get("local") == profile
    assert reloaded.last_profile == "local"

    assert reloaded.delete("local") is True
    assert reloaded.delete("local") is False
    assert ProfileStore(path).last_profile is None


def test_store_rejects_malformed_files(tmp_path):
    path = tmp_path / "profiles.json"

    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProfileError, match="not valid JSON"):
        ProfileStore(path)

    path.write_text(json.dumps({"version": 99, "profiles": {}}), encoding="utf-8")
    with pytest.raises(ProfileError, match="unsupported profile store version"):
        ProfileStore(path)

    path.write_text(json.dumps({"profiles": []}), encoding="utf-8")
    with pytest.raises(ProfileError, match="must be an object"):
        ProfileStore(path)


def test_profile_validation_rejects_bad_fields():
    with pytest.raises(ProfileError, match="profile name"):
        Profile(name="bad name")
    with pytest.raises(ProfileError, match="api must be"):
        Profile(name="ok", api="grpc")
    with pytest.raises(ProfileError, match="thinking must be"):
        Profile(name="ok", thinking="maybe")
    with pytest.raises(ProfileError, match="reasoning_effort must be"):
        Profile(name="ok", reasoning_effort="ultra")
    with pytest.raises(ProfileError, match="api_key_env must be"):
        Profile(name="ok", api_key_env="not-a-var")
    with pytest.raises(ProfileError, match="base_url must not be empty"):
        Profile(name="ok", base_url="  ")


def test_profile_roundtrip_and_masked_output():
    profile = Profile(
        name="proxy",
        base_url="https://proxy.example/v1",
        model="remote-model",
        api="responses",
        thinking="enabled",
        reasoning_effort="low",
        api_key="sk-secret-value",
    )

    data = profile.to_dict()
    assert data["api_key"] == "sk-secret-value"
    assert Profile.from_dict("proxy", data) == profile
    assert "sk-secret-value" not in json.dumps(profile.masked())


def test_profile_from_config_does_not_store_literal_key():
    config = LLMConfig(
        base_url="https://proxy.example/v1",
        api_key="sk-secret-value",
        model="remote-model",
        api="responses",
        thinking="enabled",
        reasoning_effort="low",
    )

    profile = Profile.from_config(
        "proxy", config, api_key_env="PROXY_API_KEY"
    )

    assert profile.api_key is None
    assert profile.api_key_env == "PROXY_API_KEY"
    assert profile.base_url == "https://proxy.example/v1"
    assert profile.model == "remote-model"
    assert profile.api == "responses"
    assert profile.thinking == "enabled"
    assert profile.reasoning_effort == "low"


def test_store_rejects_unknown_profile_fields(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps(
            {"version": 1, "profiles": {"x": {"model": "m", "typo_field": 1}}}
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProfileError, match="unknown fields"):
        ProfileStore(path)


def test_store_rejects_non_string_profile_fields(tmp_path):
    path = tmp_path / "profiles.json"

    path.write_text(
        json.dumps({"version": 1, "profiles": {"x": {"base_url": 1}}}),
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="base_url must be a string"):
        ProfileStore(path)

    path.write_text(
        json.dumps({"version": 1, "profiles": {"x": {"api_key_env": 3}}}),
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="api_key_env must be a string"):
        ProfileStore(path)


def test_store_rejects_non_utf8_file(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_bytes(b'\xff\xfe\x00{"version": 1, "profiles": {}}')

    with pytest.raises(ProfileError, match="not valid UTF-8"):
        ProfileStore(path)


def test_profile_rejects_non_string_fields_directly():
    with pytest.raises(ProfileError, match="api_key_env must be a string"):
        Profile(name="ok", api_key_env=3)  # type: ignore[arg-type]
    with pytest.raises(ProfileError, match="reasoning_effort must be a string"):
        Profile(name="ok", reasoning_effort=1)  # type: ignore[arg-type]


def test_save_uses_sibling_temp_file_and_cleans_up(tmp_path):
    path = tmp_path / "profiles.json"
    store = ProfileStore(path)

    store.upsert(Profile(name="local", model="m"))

    assert path.exists()
    assert list(tmp_path.glob("profiles.json.*.tmp")) == []


def test_mutations_roll_back_when_persistence_fails(tmp_path, monkeypatch):
    path = tmp_path / "profiles.json"
    store = ProfileStore(path)
    store.upsert(Profile(name="keep", model="m"))
    store.set_last("keep")

    def _boom():
        raise ProfileError("disk full")

    monkeypatch.setattr(store, "save", _boom)

    with pytest.raises(ProfileError, match="disk full"):
        store.upsert(Profile(name="new"))
    assert store.get("new") is None
    assert store.get("keep") is not None

    with pytest.raises(ProfileError, match="disk full"):
        store.delete("keep")
    assert store.get("keep") is not None
    assert store.last_profile == "keep"

    with pytest.raises(ProfileError, match="disk full"):
        store.set_last(None)
    assert store.last_profile == "keep"


def test_save_reports_unwritable_path(tmp_path, monkeypatch):
    store = ProfileStore(tmp_path / "profiles.json")

    def _fail_write(self, text, encoding=None):
        raise OSError("disk full")

    monkeypatch.setattr("pathlib.Path.write_text", _fail_write)
    with pytest.raises(ProfileError, match="cannot write profile store"):
        store.upsert(Profile(name="local"))
