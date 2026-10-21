"""Tests for the minimal OpenAI-compatible streaming client."""

import json

import pytest

from rs_agent.llm import (
    Done,
    LLMClient,
    LLMConfig,
    LLMError,
    ReasoningDelta,
    TextDelta,
    ToolCall,
    build_request,
    load_env_file,
    parse_chat_stream,
    parse_responses_stream,
    resolve_config,
)

CHAT_MESSAGES = [
    {"role": "system", "content": "你是检查助手。"},
    {"role": "user", "content": "看看 examples/sample.ppm"},
    {
        "role": "assistant",
        "content": "我来检查。",
        "reasoning_content": "需要调用 inspect_image。",
        "tool_calls": [
            {
                "id": "call_1",
                "name": "inspect_image",
                "arguments": '{"path": "examples/sample.ppm"}',
            }
        ],
    },
    {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"ok": true, "result": {"width": 2, "height": 2}}',
    },
]

TOOL_SPECS = [
    {
        "name": "inspect_image",
        "description": "检查图片",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    }
]


def _chat_chunks(*chunks: dict) -> list[bytes]:
    lines = [
        f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode() for chunk in chunks
    ]
    lines.append(b"data: [DONE]\n\n")
    return lines


def _responses_lines(*events: tuple[str, dict]) -> list[bytes]:
    lines: list[bytes] = []
    for name, payload in events:
        lines.append(f"event: {name}\n".encode())
        lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())
    return lines


class FakeTransport:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines
        self.requests: list[tuple[str, dict, bytes]] = []

    def open(self, url: str, headers: dict, body: bytes):
        self.requests.append((url, dict(headers), body))
        return iter(self.lines)


class RaisingTransport:
    def open(self, url: str, headers: dict, body: bytes):
        raise LLMError("upstream exploded", status=503)


class InterruptingTransport:
    def open(self, url: str, headers: dict, body: bytes):
        def lines():
            yield b'data: {"choices":[{"index":0,"delta":{"content":"half"}}]}\n\n'
            raise ConnectionResetError("reset by peer")

        return lines()


def test_parse_chat_stream_normalizes_events():
    chunks = [
        {"choices": [{"index": 0, "delta": {"reasoning_content": "先看看"}}]},
        {"choices": [{"index": 0, "delta": {"content": "好的"}}]},
        {"choices": [{"index": 0, "delta": {"content": "，我检查一下。"}}]},
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "inspect_image",
                                    "arguments": '{"path": ',
                                },
                            }
                        ]
                    },
                }
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {
                                    "arguments": '"examples/sample.ppm"}'
                                },
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
    ]

    events = list(parse_chat_stream(_chat_chunks(*chunks)))

    assert events == [
        ReasoningDelta("先看看"),
        TextDelta("好的"),
        TextDelta("，我检查一下。"),
        ToolCall("call_1", "inspect_image", '{"path": "examples/sample.ppm"}'),
        Done(
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            finish_reason="tool_calls",
        ),
    ]


def test_parse_responses_stream_normalizes_events():
    lines = _responses_lines(
        (
            "response.output_text.delta",
            {"type": "response.output_text.delta", "delta": "图片"},
        ),
        (
            "response.reasoning_text.delta",
            {"type": "response.reasoning_text.delta", "delta": "思考中"},
        ),
        (
            "response.output_item.added",
            {
                "type": "response.output_item.added",
                "output_index": 1,
                "item": {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "inspect_image",
                    "arguments": "",
                },
            },
        ),
        (
            "response.function_call_arguments.delta",
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_1",
                "delta": '{"path": "examples/sample.ppm"}',
            },
        ),
        (
            "response.output_item.done",
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "inspect_image",
                    "arguments": '{"path": "examples/sample.ppm"}',
                },
            },
        ),
        (
            "response.completed",
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
        ),
    )

    events = list(parse_responses_stream(lines))

    assert events == [
        TextDelta("图片"),
        ReasoningDelta("思考中"),
        ToolCall("call_1", "inspect_image", '{"path": "examples/sample.ppm"}'),
        Done(
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="completed",
        ),
    ]


def test_build_chat_request_maps_messages_tools_and_sampling():
    config = LLMConfig(
        api_key="k",
        api="chat",
        max_tokens=64,
        temperature=0.2,
        thinking="disabled",
    )
    url, body = build_request(config, CHAT_MESSAGES, TOOL_SPECS)

    assert url == "https://api.deepseek.com/chat/completions"
    assert body["model"] == "deepseek-flash"
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["max_tokens"] == 64
    assert body["temperature"] == 0.2
    assert body["thinking"] == {"type": "disabled"}
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["function"]["name"] == "inspect_image"
    assert body["tools"][0]["function"]["parameters"]["required"] == ["path"]
    assert body["tool_choice"] == "auto"

    messages = body["messages"]
    assert messages[0] == {"role": "system", "content": "你是检查助手。"}
    assert messages[1]["role"] == "user"
    assistant = messages[2]
    assert assistant["reasoning_content"] == "需要调用 inspect_image。"
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert assistant["tool_calls"][0]["function"]["name"] == "inspect_image"
    assert messages[3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"ok": true, "result": {"width": 2, "height": 2}}',
    }


def test_build_responses_request_splits_instructions_from_items():
    config = LLMConfig(
        api_key="k",
        api="responses",
        max_tokens=64,
        thinking="disabled",
    )
    url, body = build_request(config, CHAT_MESSAGES, TOOL_SPECS)

    assert url == "https://api.deepseek.com/responses"
    assert body["instructions"] == "你是检查助手。"
    assert body["stream"] is True
    assert body["max_output_tokens"] == 64
    assert body["reasoning"] == {"effort": "none"}
    assert body["tools"] == [
        {
            "type": "function",
            "name": "inspect_image",
            "description": "检查图片",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
    ]

    items = body["input"]
    assert items[0] == {"role": "user", "content": "看看 examples/sample.ppm"}
    assert items[1] == {
        "role": "assistant",
        "content": [{"type": "output_text", "text": "我来检查。"}],
    }
    assert items[2] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "inspect_image",
        "arguments": '{"path": "examples/sample.ppm"}',
    }
    assert items[3] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": '{"ok": true, "result": {"width": 2, "height": 2}}',
    }


def test_response_tools_use_top_level_name():
    config = LLMConfig(api_key="k", api="responses", thinking="disabled")
    _url, body = build_request(config, [{"role": "user", "content": "hi"}], TOOL_SPECS)
    flat = body["tools"][0]
    assert flat["name"] == "inspect_image"
    assert "function" not in flat


def test_resolve_config_precedence_and_deepseek_fallback():
    env = {
        "RS_AGENT_BASE_URL": "https://proxy.example/v1",
        "RS_AGENT_API_KEY": "env-key",
        "RS_AGENT_MODEL": "env-model",
        "RS_AGENT_API": "responses",
        "DEEPSEEK_API_KEY": "deepseek-key",
    }
    config = resolve_config(env=env)
    assert config.base_url == "https://proxy.example/v1"
    assert config.api_key == "env-key"
    assert config.model == "env-model"
    assert config.api == "responses"

    fallback = resolve_config(env={"DEEPSEEK_API_KEY": "deepseek-key"})
    assert fallback.api_key == "deepseek-key"
    assert fallback.base_url == "https://api.deepseek.com"
    assert fallback.model == "deepseek-flash"
    assert fallback.api == "chat"

    explicit = resolve_config(
        base_url="https://custom.example",
        api_key="flag-key",
        model="custom-model",
        api="chat",
        env=env,
    )
    assert explicit.base_url == "https://custom.example"
    assert explicit.api_key == "flag-key"
    assert explicit.model == "custom-model"
    assert explicit.api == "chat"


def test_client_streams_through_fake_transport_and_sets_headers():
    transport = FakeTransport(
        _chat_chunks(
            {"choices": [{"index": 0, "delta": {"content": "OK"}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        )
    )
    client = LLMClient(
        LLMConfig(base_url="https://custom.example/v1/", api_key="secret", api="chat"),
        transport=transport,
    )

    events = list(client.send([{"role": "user", "content": "hi"}]))

    assert events[0] == TextDelta("OK")
    assert isinstance(events[-1], Done)
    url, headers, body = transport.requests[0]
    assert url == "https://custom.example/v1/chat/completions"
    assert headers["Authorization"] == "Bearer secret"
    assert json.loads(body)["model"] == "deepseek-flash"


def test_client_requires_api_key():
    client = LLMClient(LLMConfig(api_key=""), transport=FakeTransport([]))
    with pytest.raises(LLMError, match="Missing API key"):
        next(iter(client.send([{"role": "user", "content": "hi"}])))


def test_client_propagates_transport_errors():
    client = LLMClient(
        LLMConfig(api_key="k", api="chat"), transport=RaisingTransport()
    )
    with pytest.raises(LLMError, match="upstream exploded"):
        next(iter(client.send([{"role": "user", "content": "hi"}])))


def test_read_errors_are_wrapped_as_llm_error():
    client = LLMClient(
        LLMConfig(api_key="k", api="chat"), transport=InterruptingTransport()
    )
    with pytest.raises(LLMError, match="stream interrupted"):
        list(client.send([{"role": "user", "content": "hi"}]))


def test_invalid_api_rejected():
    with pytest.raises(ValueError, match="Unsupported api"):
        LLMConfig(api="grpc")


def test_chat_stream_without_done_marker_raises():
    lines = [
        b'data: {"choices":[{"index":0,"delta":{"content":"partial"}}]}\n\n',
    ]
    with pytest.raises(LLMError, match="DONE"):
        list(parse_chat_stream(lines))


def test_chat_fatal_finish_reason_raises():
    lines = _chat_chunks(
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "insufficient_system_resource",
                }
            ]
        }
    )
    with pytest.raises(LLMError, match="insufficient_system_resource"):
        list(parse_chat_stream(lines))


def test_responses_stream_without_terminal_event_raises():
    lines = _responses_lines(
        (
            "response.output_text.delta",
            {"type": "response.output_text.delta", "delta": "partial"},
        )
    )
    with pytest.raises(LLMError, match="response.completed"):
        list(parse_responses_stream(lines))


def test_responses_failed_event_raises_with_detail():
    lines = _responses_lines(
        (
            "response.failed",
            {
                "type": "response.failed",
                "response": {
                    "status": "failed",
                    "error": {"code": "server_error", "message": "upstream overloaded"},
                },
            },
        )
    )
    with pytest.raises(LLMError, match="upstream overloaded"):
        list(parse_responses_stream(lines))


def test_load_env_file_parses_comments_quotes_and_export(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# local config\n"
        "\n"
        "DEEPSEEK_API_KEY='sk-test'\n"
        "export RS_AGENT_MODEL=deepseek-v4-pro\n"
        'RS_AGENT_API="chat"\n'
        "not a valid line\n",
        encoding="utf-8",
    )

    values = load_env_file(env_file)

    assert values == {
        "DEEPSEEK_API_KEY": "sk-test",
        "RS_AGENT_MODEL": "deepseek-v4-pro",
        "RS_AGENT_API": "chat",
    }


def test_load_env_file_missing_file_returns_empty(tmp_path):
    assert load_env_file(tmp_path / "missing.env") == {}


def test_load_env_file_handles_bom_and_inline_comments(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_bytes(
        (
            "\ufeffDEEPSEEK_API_KEY=key-with-bom\n"
            "RS_AGENT_MODEL=deepseek-flash # default model\n"
            'RS_AGENT_API="chat # still part of the value" # trailing\n'
        ).encode("utf-8")
    )

    values = load_env_file(env_file)

    assert values["DEEPSEEK_API_KEY"] == "key-with-bom"
    assert values["RS_AGENT_MODEL"] == "deepseek-flash"
    assert values["RS_AGENT_API"] == "chat # still part of the value"


def test_resolve_config_reads_dotenv_with_env_precedence(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEEPSEEK_API_KEY=from-dotenv\nRS_AGENT_MODEL=dotenv-model\n",
        encoding="utf-8",
    )

    from_dotenv = resolve_config(env_file=env_file, env={})
    assert from_dotenv.api_key == "from-dotenv"
    assert from_dotenv.model == "dotenv-model"
    assert from_dotenv.base_url == "https://api.deepseek.com"

    env_wins = resolve_config(env_file=env_file, env={"RS_AGENT_MODEL": "env-model"})
    assert env_wins.model == "env-model"
    assert env_wins.api_key == "from-dotenv"

    flags_win = resolve_config(
        env_file=env_file, api_key="flag-key", model="flag-model", env={}
    )
    assert flags_win.api_key == "flag-key"
    assert flags_win.model == "flag-model"


def test_empty_env_var_does_not_mask_dotenv_value(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=from-dotenv\n", encoding="utf-8")

    config = resolve_config(
        env_file=env_file,
        env={"DEEPSEEK_API_KEY": "", "RS_AGENT_MODEL": ""},
    )

    assert config.api_key == "from-dotenv"
    assert config.model == "deepseek-flash"


def test_resolve_config_thinking_from_env_and_dotenv():
    default = resolve_config(env={})
    assert default.thinking == "disabled"
    assert default.reasoning_effort is None

    explicit = resolve_config(
        env={"RS_AGENT_THINKING": "enabled", "RS_AGENT_REASONING_EFFORT": "low"}
    )
    assert explicit.thinking == "enabled"
    assert explicit.reasoning_effort == "low"

    effort_only = resolve_config(env={"RS_AGENT_REASONING_EFFORT": "max"})
    assert effort_only.thinking == "enabled"
    assert effort_only.reasoning_effort == "max"

    toggle_wins = resolve_config(
        thinking="disabled",
        env={"RS_AGENT_THINKING": "enabled", "RS_AGENT_REASONING_EFFORT": "high"},
    )
    assert toggle_wins.thinking == "disabled"
    assert toggle_wins.reasoning_effort is None


def test_resolve_config_thinking_from_dotenv(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "RS_AGENT_THINKING=enabled\nRS_AGENT_REASONING_EFFORT=high\n",
        encoding="utf-8",
    )

    config = resolve_config(env_file=env_file, env={})

    assert config.thinking == "enabled"
    assert config.reasoning_effort == "high"
