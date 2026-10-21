"""Persistent named configuration profiles for the chat harness.

Profiles live in a small local JSON file (``.rs_agent_profiles.json`` by
default, gitignored).  A profile stores the non-secret connection settings and,
optionally, the *name* of the environment variable that holds its API key —
keys themselves are never required to be written here.

The shape mirrors the reference implementation in DeepSeek Harness: a named
profile bundles provider settings and references credentials by env name
(``apiKeyEnv``), while the store is written atomically.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

DEFAULT_PROFILES_FILE = ".rs_agent_profiles.json"
STORE_VERSION = 1

_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_VALID_APIS = ("chat", "responses")
_VALID_THINKING = ("enabled", "disabled")
_VALID_EFFORTS = ("low", "high", "max")


class ProfileError(ValueError):
    """Raised when a profile or the profile store is malformed."""


@dataclass(frozen=True)
class Profile:
    """One named connection configuration."""

    name: str
    base_url: str | None = None
    model: str | None = None
    api: str | None = None
    thinking: str | None = None
    reasoning_effort: str | None = None
    api_key_env: str | None = None
    api_key: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME_RE.match(self.name):
            raise ProfileError(
                "profile name must be 1-64 chars of letters, digits, '.', '_' or '-'"
            )
        for field_name in (
            "base_url",
            "model",
            "api",
            "thinking",
            "reasoning_effort",
            "api_key_env",
            "api_key",
        ):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise ProfileError(
                    f"profile {self.name!r}: {field_name} must be a string"
                )
        if self.base_url is not None and not self.base_url.strip():
            raise ProfileError(f"profile {self.name!r}: base_url must not be empty")
        if self.api is not None and self.api not in _VALID_APIS:
            raise ProfileError(
                f"profile {self.name!r}: api must be one of {_VALID_APIS}"
            )
        if self.thinking is not None and self.thinking not in _VALID_THINKING:
            raise ProfileError(
                f"profile {self.name!r}: thinking must be one of {_VALID_THINKING}"
            )
        if (
            self.reasoning_effort is not None
            and self.reasoning_effort not in _VALID_EFFORTS
        ):
            raise ProfileError(
                f"profile {self.name!r}: reasoning_effort must be one of {_VALID_EFFORTS}"
            )
        if self.api_key_env is not None and not _ENV_NAME_RE.match(self.api_key_env):
            raise ProfileError(
                f"profile {self.name!r}: api_key_env must be an env var name"
            )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        del data["name"]
        return {key: value for key, value in data.items() if value is not None}

    @classmethod
    def from_dict(cls, name: str, data: Mapping[str, Any]) -> "Profile":
        if not isinstance(data, Mapping):
            raise ProfileError(f"profile {name!r} must be a JSON object")
        known = {
            "base_url",
            "model",
            "api",
            "thinking",
            "reasoning_effort",
            "api_key_env",
            "api_key",
        }
        unexpected = set(data) - known
        if unexpected:
            raise ProfileError(
                f"profile {name!r} has unknown fields: {sorted(unexpected)}"
            )
        return cls(name=name, **dict(data))

    @classmethod
    def from_config(
        cls,
        name: str,
        config: Any,
        *,
        api_key_env: str | None = None,
    ) -> "Profile":
        """Build a profile from a resolved :class:`LLMConfig` (never the key)."""
        return cls(
            name=name,
            base_url=config.base_url,
            model=config.model,
            api=config.api,
            thinking=config.thinking,
            reasoning_effort=config.reasoning_effort,
            api_key_env=api_key_env,
        )

    def masked(self) -> dict[str, Any]:
        """Return a display copy with secrets masked."""
        data = self.to_dict()
        if data.get("api_key"):
            data["api_key"] = "sk-****"
        return data


class ProfileStore:
    """Loads and atomically saves named profiles."""

    def __init__(self, path: str | Path = DEFAULT_PROFILES_FILE) -> None:
        self.path = Path(path)
        self.last_profile: str | None = None
        self.profiles: dict[str, Profile] = {}
        self._load()

    # -- persistence --------------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except OSError as exc:
            raise ProfileError(f"cannot read profile store {self.path}: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise ProfileError(
                f"profile store {self.path} is not valid UTF-8: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ProfileError(f"profile store {self.path} is not valid JSON: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise ProfileError(f"profile store {self.path} must contain a JSON object")
        version = raw.get("version", STORE_VERSION)
        if version != STORE_VERSION:
            raise ProfileError(
                f"unsupported profile store version {version!r} (expected {STORE_VERSION})"
            )
        profiles = raw.get("profiles")
        if profiles is None:
            profiles = {}
        if not isinstance(profiles, Mapping):
            raise ProfileError("profile store field 'profiles' must be an object")
        loaded: dict[str, Profile] = {}
        for name, data in profiles.items():
            loaded[name] = Profile.from_dict(name, data)
        self.profiles = loaded
        last = raw.get("last_profile")
        self.last_profile = last if isinstance(last, str) and last in loaded else None

    def save(self) -> None:
        payload = {
            "version": STORE_VERSION,
            "last_profile": self.last_profile,
            "profiles": {
                name: profile.to_dict() for name, profile in self.profiles.items()
            },
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        parent = self.path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ProfileError(
                f"cannot create profile directory {parent}: {exc}"
            ) from exc
        # A plain sibling temp file keeps the write atomic via os.replace.
        # (tempfile.NamedTemporaryFile can hang under restricted temp-dir ACLs.)
        tmp_path = parent / f"{self.path.name}.{os.getpid()}.tmp"
        try:
            tmp_path.write_text(text, encoding="utf-8")
            os.replace(tmp_path, self.path)
        except OSError as exc:
            raise ProfileError(
                f"cannot write profile store {self.path}: {exc}"
            ) from exc
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:  # pragma: no cover - best effort cleanup
                    pass

    # -- access -------------------------------------------------------------
    def names(self) -> list[str]:
        return list(self.profiles)

    def get(self, name: str) -> Profile | None:
        return self.profiles.get(name)

    def upsert(self, profile: Profile) -> None:
        self.profiles[profile.name] = profile
        self.save()

    def delete(self, name: str) -> bool:
        if name not in self.profiles:
            return False
        del self.profiles[name]
        if self.last_profile == name:
            self.last_profile = None
        self.save()
        return True

    def set_last(self, name: str | None) -> None:
        if name is not None and name not in self.profiles:
            name = None
        if self.last_profile != name:
            self.last_profile = name
            self.save()
