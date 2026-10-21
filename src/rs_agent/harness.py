"""A minimal agent harness: system prompt, tool loop and streaming output.

The harness owns the conversation history, exposes the project's inspection
tools to the model, executes requested tool calls and streams the model's
natural-language answer to a terminal.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TextIO

from rs_agent.llm import (
    Done,
    LLMClient,
    LLMError,
    ReasoningDelta,
    TextDelta,
    ToolCall,
)
from rs_agent.tools import (
    ImageInspectionError,
    RasterInspectionError,
    inspect_image,
    inspect_raster,
)

SYSTEM_PROMPT = """你是一个运行在终端里的遥感影像检查助手，由最小 Agent harness 驱动。

你可以调用以下工具：
- inspect_image(path)：用 Pillow 读取普通图片，返回格式、宽高、色彩模式、波段列表和各波段统计量（最小/最大/均值）。
- inspect_raster(path)：调用 gdalinfo 读取栅格文件的低成本元数据（驱动、尺寸、波段数、坐标系、地理变换、像元大小、四角坐标）。

工作方式：
1. 用户给出文件路径并要求查看或检查时，把用户消息里的路径原文传给工具，不要改写路径、不要自行补全目录，也绝不臆造路径。
2. 如果用户只给了文件名、工具报告不存在，就请用户补全路径后再试，不要猜测目录。
3. 工具返回 JSON 观测结果。请用简洁的中文自然语言复述关键信息（尺寸、波段、坐标系、统计量等），不要编造工具没有返回的数值。
4. 工具失败时，说明失败原因，并给出可操作的下一步建议。
5. 无需工具时直接回答。回答保持简短，适合在终端里阅读，不要输出 JSON 或 Markdown 表格。
"""

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "inspect_image",
        "description": "读取普通图片文件（PNG/JPEG/PPM 等）的元数据与各波段统计量。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "用户消息中给出的图片文件路径，原样传入（相对或绝对路径均可）",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "inspect_raster",
        "description": "调用 gdalinfo 读取 GeoTIFF 等栅格文件的紧凑元数据。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "用户消息中给出的栅格文件路径，原样传入（相对或绝对路径均可）",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
]

TOOL_IMPLEMENTATIONS = {
    "inspect_image": inspect_image,
    "inspect_raster": inspect_raster,
}

# Observations are fed back to the model as a single string. Keep a bounded
# head+tail so an unexpectedly large tool result cannot flood the context.
_MAX_OBSERVATION_CHARS = 6000
_OBSERVATION_HEAD_CHARS = 4000
_OBSERVATION_TAIL_CHARS = 1500

_TRUNCATED_FINISH_REASONS = {"length", "max_tokens", "incomplete"}


def _truncate(text: str) -> str:
    """Return ``text`` bounded to a head/tail window with an explicit notice."""
    if len(text) <= _MAX_OBSERVATION_CHARS:
        return text
    omitted = len(text) - _OBSERVATION_HEAD_CHARS - _OBSERVATION_TAIL_CHARS
    return (
        text[:_OBSERVATION_HEAD_CHARS]
        + f"\n…[观测结果过长，省略 {omitted} 个字符]…\n"
        + text[-_OBSERVATION_TAIL_CHARS:]
    )


def build_tool_specs() -> list[dict[str, Any]]:
    """Return the JSON-schema tool definitions exposed to the model."""
    return [dict(spec) for spec in TOOL_SPECS]


def execute_tool(name: str, arguments: str) -> dict[str, Any]:
    """Execute one tool call and always return a JSON-serializable observation."""
    implementation = TOOL_IMPLEMENTATIONS.get(name)
    if implementation is None:
        return {"ok": False, "tool": name, "error": f"未知工具: {name}"}

    try:
        parsed = json.loads(arguments or "{}")
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "tool": name,
            "error": f"工具参数不是合法 JSON: {exc}",
        }
    if not isinstance(parsed, dict):
        return {"ok": False, "tool": name, "error": "工具参数必须是 JSON 对象。"}

    try:
        result = implementation(**parsed)
    except (ImageInspectionError, RasterInspectionError) as exc:
        return {"ok": False, "tool": name, "error": str(exc)}
    except TypeError as exc:
        return {"ok": False, "tool": name, "error": f"工具参数不匹配: {exc}"}
    except Exception as exc:  # noqa: BLE001 - observation must never crash the loop
        return {"ok": False, "tool": name, "error": f"工具执行失败: {exc}"}

    return {"ok": True, "tool": name, "result": result}


def _serialize(observation: Mapping[str, Any]) -> str:
    return json.dumps(observation, ensure_ascii=False, default=str)


@dataclass
class Harness:
    """Owns one conversation and drives the tool-calling loop."""

    client: LLMClient
    max_steps: int = 8
    system_prompt: str = SYSTEM_PROMPT
    verbose: bool = False
    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    stderr: TextIO = field(default_factory=lambda: sys.stderr)
    tools: Sequence[Mapping[str, Any]] = field(default_factory=build_tool_specs)
    messages: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError("max_steps must be at least 1.")
        if not self.messages:
            self.messages.append({"role": "system", "content": self.system_prompt})

    # -- output helpers -----------------------------------------------------
    def _write(self, text: str) -> None:
        self.stdout.write(text)
        self.stdout.flush()

    def _trace(self, text: str) -> None:
        self.stderr.write(text + "\n")
        self.stderr.flush()

    # -- main loop ----------------------------------------------------------
    def run_turn(self, user_input: str) -> str:
        """Run one user turn to completion and return the final answer text."""
        self.messages.append({"role": "user", "content": user_input})

        for step in range(1, self.max_steps + 1):
            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            usage: Mapping[str, Any] | None = None
            finish_reason: str | None = None

            for event in self.client.send(self.messages, tools=self.tools):
                if isinstance(event, TextDelta):
                    text_parts.append(event.text)
                    self._write(event.text)
                elif isinstance(event, ReasoningDelta):
                    reasoning_parts.append(event.text)
                elif isinstance(event, ToolCall):
                    tool_calls.append(event)
                elif isinstance(event, Done):
                    usage = event.usage
                    finish_reason = event.finish_reason

            text = "".join(text_parts)
            if text:
                self._write("\n")

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": text,
            }
            if reasoning_parts:
                assistant_message["reasoning_content"] = "".join(reasoning_parts)

            if finish_reason in _TRUNCATED_FINISH_REASONS and not tool_calls:
                note = "（回答可能被截断；可增大 --max-tokens 后重试。）"
                text = text + note
                assistant_message["content"] = text
                self._write(note + "\n")

            if tool_calls and finish_reason in _TRUNCATED_FINISH_REASONS:
                # 输出被截断时工具参数可能不完整：不执行、也不写入历史，
                # 避免留下无法配对的悬空 tool call。
                note = (
                    "（模型输出被截断，本轮未执行工具调用；"
                    "可增大 --max-tokens 后重试。）"
                )
                self._write(note + "\n")
                self.messages.append(assistant_message)
                return text + note

            if tool_calls:
                assistant_message["tool_calls"] = [
                    {
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                    }
                    for call in tool_calls
                ]
            self.messages.append(assistant_message)

            if self.verbose and usage:
                self._trace(f"[usage] {json.dumps(usage, ensure_ascii=False)}")

            if not tool_calls:
                return text

            for call in tool_calls:
                self._trace(f"[tool] {call.name} {call.arguments}")
                observation = execute_tool(call.name, call.arguments)
                serialized = _truncate(_serialize(observation))
                self._trace(f"[observation] {serialized}")
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": serialized,
                    }
                )

        message = (
            f"已达到最大工具调用轮数（{self.max_steps}），"
            "我先停止本轮；请补充信息或把任务拆小后再试。"
        )
        self._write(message + "\n")
        return message
