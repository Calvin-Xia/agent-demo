"""A minimal, dependency-free OpenAI-compatible client for the DeepSeek API.

Two dialects are supported behind one interface:

* ``chat``: ``POST {base_url}/chat/completions`` (OpenAI Chat Completions).
* ``responses``: ``POST {base_url}/responses`` (OpenAI Responses).

Both dialects stream with server-sent events (SSE).  The client normalizes
them into the small event vocabulary used by :mod:`rs_agent.harness`:
``TextDelta``, ``ReasoningDelta``, ``ToolCall`` and ``Done``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_API = "chat"
SUPPORTED_APIS = ("chat", "responses")

_ENV_PREFIX = "RS_AGENT_"

# Chat finish reasons that mean the model did not complete a usable answer.
_FATAL_CHAT_FINISH_REASONS = {"content_filter", "insufficient_system_resource"}


class LLMError(RuntimeError):
    """Raised when a request to the remote API cannot be completed."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        body: str | None = None,
    ) -> None:
        self.status = status
        self.body = body
        super().__init__(message)


@dataclass(frozen=True)
class TextDelta:
    """An incremental chunk of the model's visible answer."""

    text: str


@dataclass(frozen=True)
class ReasoningDelta:
    """An incremental chunk of the model's chain of thought."""

    text: str


@dataclass(frozen=True)
class ToolCall:
    """A complete tool call assembled from the stream."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class Done:
    """The stream has finished."""

    usage: Mapping[str, Any] | None = None
    finish_reason: str | None = None


LLMEvent = TextDelta | ReasoningDelta | ToolCall | Done


@dataclass(frozen=True)
class LLMConfig:
    """Everything needed to talk to one OpenAI-compatible endpoint."""

    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    model: str = DEFAULT_MODEL
    api: str = DEFAULT_API
    timeout: float = 120.0
    max_tokens: int | None = None
    temperature: float | None = None
    thinking: str | None = None
    reasoning_effort: str | None = None

    def __post_init__(self) -> None:
        if self.api not in SUPPORTED_APIS:
            raise ValueError(
                f"Unsupported api {self.api!r}; expected one of {SUPPORTED_APIS}."
            )
        if not self.base_url.strip():
            raise ValueError("base_url must not be empty.")
        if self.thinking not in (None, "enabled", "disabled"):
            raise ValueError("thinking must be None, 'enabled' or 'disabled'.")


def load_env_file(path: str | Path) -> dict[str, str]:
    """Parse a minimal ``.env`` file into a mapping.

    Supported syntax: ``KEY=VALUE`` lines, ``#`` comments, optional ``export``
    prefix, blank lines, single/double quoted values (quotes stripped, inner
    ``#`` kept), inline `` # comment`` for unquoted values, and a UTF-8 BOM
    (``.env`` files saved by Notepad still load). Missing or unreadable files
    yield an empty mapping, so a local ``.env`` is always optional.
    """
    values: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return values
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        quote = value[0] if value and value[0] in ("'", '"') else ""
        if quote:
            end = value.find(quote, 1)
            value = value[1:end] if end != -1 else value[1:]
        else:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def resolve_config(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    api: str | None = None,
    thinking: str | None = None,
    reasoning_effort: str | None = None,
    env: Mapping[str, str] | None = None,
    env_file: str | Path | None = None,
    **overrides: Any,
) -> LLMConfig:
    """Resolve configuration from explicit values, environment and defaults.

    Precedence: explicit arguments > process environment (or ``env`` mapping)
    > optional ``.env`` file > defaults.  ``DEEPSEEK_*`` variables are accepted
    as a fallback so the demo can reuse an existing DeepSeek setup.
    """
    source: dict[str, str] = {}
    if env_file is not None:
        source.update(load_env_file(env_file))
    effective_env = os.environ if env is None else env
    for name, value in effective_env.items():
        # An empty environment variable (common on Windows shells) should not
        # mask a value that is present in the local .env file.
        if value:
            source[name] = value

    def pick(explicit: str | None, *names: str) -> str | None:
        if explicit:
            return explicit
        for name in names:
            value = source.get(name)
            if value:
                return value
        return None

    resolved_base = pick(
        base_url, f"{_ENV_PREFIX}BASE_URL", "DEEPSEEK_BASE_URL"
    ) or DEFAULT_BASE_URL
    resolved_key = pick(
        api_key, f"{_ENV_PREFIX}API_KEY", "DEEPSEEK_API_KEY"
    ) or ""
    resolved_model = pick(model, f"{_ENV_PREFIX}MODEL") or DEFAULT_MODEL
    resolved_api = pick(api, f"{_ENV_PREFIX}API") or DEFAULT_API

    resolved_effort = pick(
        reasoning_effort, f"{_ENV_PREFIX}REASONING_EFFORT"
    )
    configured_thinking = pick(thinking, f"{_ENV_PREFIX}THINKING")
    if configured_thinking:
        resolved_thinking = configured_thinking
    elif resolved_effort:
        # Setting an effort without an explicit toggle implies thinking mode.
        resolved_thinking = "enabled"
    else:
        resolved_thinking = "disabled"
    if resolved_thinking == "disabled":
        resolved_effort = None

    return LLMConfig(
        base_url=resolved_base.rstrip("/"),
        api_key=resolved_key,
        model=resolved_model,
        api=resolved_api,
        thinking=resolved_thinking,
        reasoning_effort=resolved_effort,
        **overrides,
    )


class Transport(Protocol):
    """Opens a streaming HTTP request and yields raw SSE lines."""

    def open(
        self, url: str, headers: Mapping[str, str], body: bytes
    ) -> Iterator[bytes]:  # pragma: no cover - protocol definition
        ...


class UrllibTransport:
    """Standard-library streaming transport (no third-party dependencies)."""

    def __init__(self, timeout: float = 120.0) -> None:
        self.timeout = timeout

    def open(
        self, url: str, headers: Mapping[str, str], body: bytes
    ) -> Iterator[bytes]:
        request = urllib.request.Request(
            url, data=body, headers=dict(headers), method="POST"
        )
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = _read_error_body(exc)
            raise LLMError(
                f"API request failed with HTTP {exc.code}: {detail}",
                status=exc.code,
                body=detail,
            ) from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"API request failed: {exc.reason}") from exc
        return response  # type: ignore[return-value]


def _read_error_body(response: Any) -> str:
    try:
        raw = response.read()
    except Exception:  # pragma: no cover - defensive
        return ""
    return raw.decode("utf-8", "replace")[:2000]


# ---------------------------------------------------------------------------
# Request building
# ---------------------------------------------------------------------------


def _chat_messages(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Convert canonical messages into Chat Completions messages."""
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role in ("system", "user"):
            converted.append({"role": role, "content": message.get("content", "")})
        elif role == "assistant":
            item: dict[str, Any] = {
                "role": "assistant",
                "content": message.get("content") or "",
            }
            reasoning = message.get("reasoning_content")
            if reasoning:
                item["reasoning_content"] = reasoning
            calls = message.get("tool_calls") or []
            if calls:
                item["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": call["arguments"],
                        },
                    }
                    for call in calls
                ]
            converted.append(item)
        elif role == "tool":
            converted.append(
                {
                    "role": "tool",
                    "tool_call_id": message["tool_call_id"],
                    "content": message.get("content", ""),
                }
            )
        else:  # pragma: no cover - guard for future code
            raise ValueError(f"Unsupported canonical message role: {role!r}")
    return converted


def _responses_input(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Split canonical messages into ``instructions`` and Responses input items."""
    instructions: list[str] = []
    items: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "system":
            instructions.append(str(message.get("content", "")))
        elif role == "user":
            items.append({"role": "user", "content": message.get("content", "")})
        elif role == "assistant":
            content = message.get("content") or ""
            if content:
                items.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": content}],
                    }
                )
            for call in message.get("tool_calls") or []:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call["id"],
                        "name": call["name"],
                        "arguments": call["arguments"],
                    }
                )
        elif role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": message.get("content", ""),
                }
            )
        else:  # pragma: no cover - guard for future code
            raise ValueError(f"Unsupported canonical message role: {role!r}")
    joined = "\n\n".join(part for part in instructions if part) or None
    return joined, items


def _chat_tools(tools: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters", {"type": "object"}),
            },
        }
        for tool in tools or []
    ]


def _responses_tools(tools: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters", {"type": "object"}),
        }
        for tool in tools or []
    ]


def _apply_sampling(body: dict[str, Any], config: LLMConfig, token_key: str) -> None:
    if config.max_tokens is not None:
        body[token_key] = config.max_tokens
    if config.temperature is not None:
        body["temperature"] = config.temperature


def build_request(
    config: LLMConfig,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return ``(url, json_body)`` for one streaming request."""
    endpoint = "/chat/completions" if config.api == "chat" else "/responses"
    url = config.base_url.rstrip("/") + endpoint

    if config.api == "chat":
        body: dict[str, Any] = {
            "model": config.model,
            "messages": _chat_messages(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = _chat_tools(tools)
            body["tool_choice"] = "auto"
        if config.thinking is not None:
            body["thinking"] = {"type": config.thinking}
        if config.reasoning_effort is not None:
            body["reasoning_effort"] = config.reasoning_effort
        _apply_sampling(body, config, "max_tokens")
    else:
        instructions, items = _responses_input(messages)
        body = {
            "model": config.model,
            "input": items,
            "stream": True,
        }
        if instructions:
            body["instructions"] = instructions
        if tools:
            body["tools"] = _responses_tools(tools)
            body["tool_choice"] = "auto"
        reasoning: dict[str, Any] = {}
        if config.thinking == "disabled":
            reasoning["effort"] = "none"
        elif config.thinking == "enabled":
            reasoning["effort"] = config.reasoning_effort or "high"
        elif config.reasoning_effort is not None:
            reasoning["effort"] = config.reasoning_effort
        if reasoning:
            body["reasoning"] = reasoning
        _apply_sampling(body, config, "max_output_tokens")

    return url, body


# ---------------------------------------------------------------------------
# Streaming parsers
# ---------------------------------------------------------------------------


def _iter_sse(lines: Iterable[bytes | str]) -> Iterator[tuple[str | None, str]]:
    """Yield ``(event_name, data)`` pairs from SSE lines.

    Accepts both true line iterators (``http.client`` responses) and chunks
    that contain several newline-separated lines.
    """
    event: str | None = None
    data_lines: list[str] = []

    def flush() -> Iterator[tuple[str | None, str]]:
        nonlocal event, data_lines
        if data_lines:
            yield event, "\n".join(data_lines)
            event = None
            data_lines = []

    for raw in lines:
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        for line in text.split("\n"):
            line = line.rstrip("\r")
            if not line:
                yield from flush()
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
    yield from flush()


def parse_chat_stream(lines: Iterable[bytes | str]) -> Iterator[LLMEvent]:
    """Parse an OpenAI/DeepSeek Chat Completions SSE stream."""
    usage: Mapping[str, Any] | None = None
    finish_reason: str | None = None
    calls: dict[int, dict[str, str]] = {}
    completed = False

    for _event, data in _iter_sse(lines):
        if data == "[DONE]":
            completed = True
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        if chunk.get("usage"):
            usage = chunk["usage"]
        choices = chunk.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        delta = choice.get("delta") or {}
        reasoning = delta.get("reasoning_content")
        if reasoning:
            yield ReasoningDelta(reasoning)
        content = delta.get("content")
        if content:
            yield TextDelta(content)
        for call in delta.get("tool_calls") or []:
            index = int(call.get("index", 0))
            entry = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if call.get("id"):
                entry["id"] = call["id"]
            function = call.get("function") or {}
            if function.get("name"):
                entry["name"] = function["name"]
            if function.get("arguments"):
                entry["arguments"] += function["arguments"]
        if choice.get("finish_reason"):
            finish_reason = choice["finish_reason"]

    if finish_reason in _FATAL_CHAT_FINISH_REASONS:
        raise LLMError(f"model stopped with finish_reason={finish_reason!r}")
    if not completed:
        raise LLMError(
            "stream ended before the [DONE] marker; the answer may be incomplete"
        )

    for index in sorted(calls):
        entry = calls[index]
        yield ToolCall(
            id=entry["id"] or f"call_{index}",
            name=entry["name"],
            arguments=entry["arguments"] or "{}",
        )
    yield Done(usage=usage, finish_reason=finish_reason)


def parse_responses_stream(lines: Iterable[bytes | str]) -> Iterator[LLMEvent]:
    """Parse an OpenAI/DeepSeek Responses API semantic SSE stream."""
    usage: Mapping[str, Any] | None = None
    status: str | None = None
    calls: dict[str, dict[str, str]] = {}
    order: list[str] = []
    terminated = False

    for event, data in _iter_sse(lines):
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        event_type = payload.get("type") or event
        if event_type == "response.output_text.delta":
            delta = payload.get("delta") or ""
            if delta:
                yield TextDelta(delta)
        elif event_type == "response.reasoning_text.delta":
            delta = payload.get("delta") or ""
            if delta:
                yield ReasoningDelta(delta)
        elif event_type == "response.output_item.added":
            item = payload.get("item") or {}
            if item.get("type") == "function_call":
                key = item.get("id") or f"out_{payload.get('output_index', len(order))}"
                if key not in calls:
                    order.append(key)
                calls[key] = {
                    "id": item.get("call_id") or "",
                    "name": item.get("name") or "",
                    "arguments": item.get("arguments") or "",
                }
        elif event_type == "response.function_call_arguments.delta":
            key = payload.get("item_id") or f"out_{payload.get('output_index', 0)}"
            entry = calls.get(key)
            if entry is None:
                entry = {"id": "", "name": "", "arguments": ""}
                calls[key] = entry
                order.append(key)
            entry["arguments"] += payload.get("delta") or ""
        elif event_type == "response.output_item.done":
            item = payload.get("item") or {}
            if item.get("type") == "function_call":
                key = item.get("id") or f"out_{payload.get('output_index', 0)}"
                entry = calls.get(key)
                if entry is None:
                    entry = {"id": "", "name": "", "arguments": ""}
                    calls[key] = entry
                    order.append(key)
                entry["id"] = item.get("call_id") or entry["id"]
                entry["name"] = item.get("name") or entry["name"]
                entry["arguments"] = item.get("arguments") or entry["arguments"]
        elif event_type == "response.failed":
            response = payload.get("response") or {}
            error = response.get("error") or payload.get("error") or {}
            if isinstance(error, Mapping):
                detail = error.get("message") or error.get("code") or "unknown error"
            else:
                detail = str(error)
            raise LLMError(f"response failed: {detail}")
        elif event_type in ("response.completed", "response.incomplete"):
            response = payload.get("response") or {}
            usage = response.get("usage") or usage
            status = response.get("status") or event_type.rsplit(".", 1)[-1]
            terminated = True
            break

    if not terminated:
        raise LLMError(
            "stream ended before response.completed; the answer may be incomplete"
        )

    for key in order:
        entry = calls[key]
        yield ToolCall(
            id=entry["id"] or key,
            name=entry["name"],
            arguments=entry["arguments"] or "{}",
        )
    yield Done(usage=usage, finish_reason=status)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class LLMClient:
    """Streams normalized events from one OpenAI-compatible endpoint."""

    def __init__(
        self,
        config: LLMConfig | None = None,
        *,
        transport: Transport | None = None,
    ) -> None:
        self.config = config if config is not None else resolve_config()
        self._transport = transport or UrllibTransport(self.config.timeout)

    def send(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] | None = None,
    ) -> Iterator[LLMEvent]:
        """Stream one model turn as normalized events."""
        if not self.config.api_key:
            raise LLMError(
                "Missing API key: pass --api-key or set RS_AGENT_API_KEY/DEEPSEEK_API_KEY."
            )
        url, body = build_request(self.config, messages, tools)
        payload = json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "rs-agent-minimal/0.1",
        }
        response = self._transport.open(url, headers, payload)
        try:
            if self.config.api == "chat":
                yield from parse_chat_stream(response)
            else:
                yield from parse_responses_stream(response)
        except (urllib.error.URLError, OSError) as exc:
            raise LLMError(f"stream interrupted: {exc}") from exc
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
