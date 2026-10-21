"""Tests for the minimal agent harness loop."""

import io
import json

from rs_agent.harness import Harness, build_tool_specs, execute_tool
from rs_agent.llm import Done, TextDelta, ToolCall


class FakeClient:
    """Yields scripted events and records the canonical messages it receives."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    def send(self, messages, tools=None):
        self.calls.append((json.loads(json.dumps(messages)), tools))
        if not self.turns:
            raise AssertionError("FakeClient.send called more times than scripted")
        events = self.turns.pop(0)
        yield from events


def _harness(client, **kwargs):
    stdout = kwargs.pop("stdout", io.StringIO())
    stderr = kwargs.pop("stderr", io.StringIO())
    harness = Harness(client, stdout=stdout, stderr=stderr, **kwargs)
    return harness, stdout, stderr


def test_tool_loop_executes_tool_and_returns_natural_language():
    client = FakeClient(
        [
            [
                ToolCall(
                    "call_1",
                    "inspect_image",
                    '{"path": "examples/sample.ppm"}',
                ),
                Done(),
            ],
            [
                TextDelta("这是一张 PPM 图片，"),
                TextDelta("宽 2 像素、高 2 像素。"),
                Done(usage={"total_tokens": 42}),
            ],
        ]
    )
    harness, stdout, stderr = _harness(client)

    answer = harness.run_turn("帮我检查 examples/sample.ppm")

    assert answer == "这是一张 PPM 图片，宽 2 像素、高 2 像素。"
    assert stdout.getvalue() == answer + "\n"

    first_messages, first_tools = client.calls[0]
    assert first_messages[-1] == {"role": "user", "content": "帮我检查 examples/sample.ppm"}
    assert first_tools[0]["name"] == "inspect_image"

    second_messages, _ = client.calls[1]
    tool_message = second_messages[-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call_1"
    observation = json.loads(tool_message["content"])
    assert observation["ok"] is True
    assert observation["result"]["width"] == 2
    assert observation["result"]["height"] == 2

    roles = [message["role"] for message in harness.messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    assert harness.messages[2]["tool_calls"][0]["name"] == "inspect_image"
    assert harness.messages[-1]["content"] == answer
    assert "[tool] inspect_image" in stderr.getvalue()
    assert "[observation]" in stderr.getvalue()


def test_run_turn_stops_at_max_steps():
    turn = [
        ToolCall("call_x", "inspect_image", '{"path": "examples/sample.ppm"}'),
        Done(),
    ]
    client = FakeClient([turn, turn, turn])
    harness, stdout, _stderr = _harness(client, max_steps=2)

    answer = harness.run_turn("一直调用工具")

    assert "最大工具调用轮数" in answer
    assert len(client.calls) == 2
    assert "最大工具调用轮数" in stdout.getvalue()


def test_unknown_tool_becomes_observation_error():
    client = FakeClient(
        [
            [ToolCall("call_1", "delete_everything", "{}"), Done()],
            [TextDelta("这个工具不可用。"), Done()],
        ]
    )
    harness, _stdout, _stderr = _harness(client)

    answer = harness.run_turn("删掉所有文件")

    assert answer == "这个工具不可用。"
    tool_message = harness.messages[3]
    assert json.loads(tool_message["content"])["ok"] is False
    assert "未知工具" in json.loads(tool_message["content"])["error"]


def test_multiple_tool_calls_are_all_executed():
    client = FakeClient(
        [
            [
                ToolCall("call_1", "inspect_image", '{"path": "examples/sample.ppm"}'),
                ToolCall("call_2", "inspect_image", '{"path": "examples/sample.ppm"}'),
                Done(),
            ],
            [TextDelta("完成。"), Done()],
        ]
    )
    harness, _stdout, _stderr = _harness(client)

    harness.run_turn("检查两次")

    tool_messages = [
        message for message in harness.messages if message["role"] == "tool"
    ]
    assert [message["tool_call_id"] for message in tool_messages] == ["call_1", "call_2"]


def test_tool_specs_match_existing_tools():
    specs = build_tool_specs()
    assert [spec["name"] for spec in specs] == ["inspect_image", "inspect_raster"]
    for spec in specs:
        assert spec["parameters"]["required"] == ["path"]


def test_execute_tool_handles_missing_file_and_bad_arguments():
    missing = execute_tool("inspect_image", '{"path": "definitely-missing.png"}')
    assert missing["ok"] is False
    assert "does not exist" in missing["error"]

    bad_json = execute_tool("inspect_image", "not json")
    assert bad_json["ok"] is False
    assert "JSON" in bad_json["error"]

    wrong_args = execute_tool("inspect_image", '{"nope": 1}')
    assert wrong_args["ok"] is False

    unknown = execute_tool("magic", "{}")
    assert unknown["ok"] is False
    assert "未知工具" in unknown["error"]


def test_long_observation_is_truncated_with_explicit_notice():
    import rs_agent.harness as harness_module

    original = harness_module.TOOL_IMPLEMENTATIONS["inspect_image"]
    harness_module.TOOL_IMPLEMENTATIONS["inspect_image"] = lambda path: {"big": "x" * 20000}
    try:
        client = FakeClient(
            [
                [ToolCall("call_1", "inspect_image", '{"path": "x"}'), Done()],
                [TextDelta("收到。"), Done()],
            ]
        )
        harness, _stdout, stderr = _harness(client)

        harness.run_turn("检查")

        tool_message = harness.messages[3]["content"]
        assert "省略" in tool_message
        assert len(tool_message) < 8000
        assert "省略" in stderr.getvalue()
    finally:
        harness_module.TOOL_IMPLEMENTATIONS["inspect_image"] = original


def test_truncated_generation_skips_unreliable_tool_calls():
    client = FakeClient(
        [
            [
                TextDelta("我先"),
                ToolCall("call_1", "inspect_image", '{"path": "examples/sample.ppm"'),
                Done(finish_reason="length"),
            ]
        ]
    )
    harness, _stdout, _stderr = _harness(client)

    answer = harness.run_turn("检查 examples/sample.ppm")

    assert "截断" in answer
    assert len(client.calls) == 1
    roles = [message["role"] for message in harness.messages]
    assert "tool" not in roles
    assert harness.messages[-1].get("tool_calls") is None


def test_truncated_text_without_tool_calls_gets_notice():
    client = FakeClient(
        [[TextDelta("这是半截回答"), Done(finish_reason="length")]]
    )
    harness, stdout, _stderr = _harness(client)

    answer = harness.run_turn("讲个长故事")

    assert answer.startswith("这是半截回答")
    assert "截断" in answer
    assert "截断" in stdout.getvalue()
    assert len(client.calls) == 1
