# 最简 Agent Harness MVP 设计说明

本文记录 `python -m rs_agent chat` 背后那条最小 Agent 循环的设计：它由哪些部件组成、消息如何在两种 OpenAI 兼容线格式之间映射、流式输出如何归一化，以及刻意没有做的事。

目标很窄：在普通终端里输入一句提示词，模型根据提示词决定是否调用现有的 `inspect_image` / `inspect_raster` 工具，再用自然语言把工具返回的信息讲出来。不做 TUI，不做多 Agent；会话不持久化（只有配置档案会落盘，方便切换 provider）。

## 组件构成

```text
┌──────────────────────────── __main__.py ────────────────────────────┐
│ chat 子命令：参数解析、配置解析、一次性 / 交互式 REPL、斜杠命令、退出码 │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ LLMClient + Harness
┌───────────────────────────────▼─────────────────────────────────────┐
│ harness.py                                                          │
│   SYSTEM_PROMPT（系统提示词）                                       │
│   TOOL_SPECS（工具的 JSON Schema）                                  │
│   execute_tool()（参数校验 + 异常翻译成观测）                        │
│   Harness.run_turn()（流式输出 → 工具循环 → 自然语言回答）           │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ 规范化事件：TextDelta / ReasoningDelta / ToolCall / Done
┌───────────────────────────────▼─────────────────────────────────────┐
│ llm.py                                                              │
│   LLMConfig / resolve_config()（base_url、key、model、方言）         │
│   build_request()（canonical 消息 → chat 或 responses 请求体）       │
│   parse_chat_stream() / parse_responses_stream()（SSE → 事件）       │
│   Transport / UrllibTransport（标准库流式 HTTP）                     │
└─────────────────────────────────────────────────────────────────────┘
                                │ HTTPS + Bearer
                         OpenAI 兼容端点（默认 DeepSeek）

另有 profiles.py 作为可选的持久化层：Profile / ProfileStore 读写命名档案，
由 __main__.py 的斜杠命令与 --profile 参数消费（见「配置档案」一节）。
```

四个部件各自只做一件事，没有框架抽象：配置是纯函数，请求构建是纯函数，两个 SSE 解析器是纯生成器，Harness 只负责状态与循环；profiles.py 只负责读写 JSON 档案。

## 规范消息模型

Harness 内部只维护一种消息结构，两种方言在发请求时才转换：

| role | 字段 | 含义 |
| --- | --- | --- |
| `system` | `content` | 系统提示词 |
| `user` | `content` | 用户输入 |
| `assistant` | `content`、`tool_calls[]`、可选 `reasoning_content` | 模型某轮的输出 |
| `tool` | `tool_call_id`、`content` | 工具观测结果（JSON 字符串） |

`tool_calls[]` 的每一项是 `{id, name, arguments}`，`arguments` 保持模型返回的 JSON 字符串，由执行层解析和校验。

### chat/completions 映射

- `system` / `user` → 原样放入 `messages`；
- `assistant` → `{role, content, reasoning_content?, tool_calls: [{id, type: "function", function: {name, arguments}}]}`；
- `tool` → `{role: "tool", tool_call_id, content}`；
- 工具定义 → `[{type: "function", function: {name, description, parameters}}]`。

### responses 映射

- `system` → 拼成 `instructions`（多个 system 用空行连接）；
- `user` → `{role: "user", content}`；
- `assistant` 文本 → `{role: "assistant", content: [{type: "output_text", text}]}`，工具调用拆成独立的 `{type: "function_call", call_id, name, arguments}` 项；
- `tool` → `{type: "function_call_output", call_id, output}`；
- 工具定义 → 扁平的 `{type: "function", name, description, parameters}`。

responses 是无状态接口，每轮都重发完整 `input`；不依赖 `previous_response_id`。

## 流式事件归一化

两种 SSE 的语法和语义不同，解析器把它们收敛成 4 类事件：

| 事件 | chat 来源 | responses 来源 |
| --- | --- | --- |
| `TextDelta` | `choices[0].delta.content` | `response.output_text.delta` |
| `ReasoningDelta` | `choices[0].delta.reasoning_content` | `response.reasoning_text.delta` |
| `ToolCall` | `delta.tool_calls[i]` 按 `index` 拼接，流结束时一次性产出 | `output_item.added` + `function_call_arguments.delta` + `output_item.done`，流结束时一次性产出 |
| `Done` | `finish_reason` 与 usage chunk，`data: [DONE]` 结束 | `response.completed/incomplete/failed` 终态事件及其 `usage`，没有 `[DONE]` |

工具调用必须等参数流拼接完才能执行，因此解析器先缓存、再在流尾产出完整的 `ToolCall`，Harness 收到完整参数后才进入工具执行。

SDK 层不打印任何东西：`LLMClient.send()` 只产事件，终端输出完全由 Harness 决定（stdout 放自然语言，stderr 放工具轨迹与 token 用量）。

**流完整性**：chat 流必须以 `data: [DONE]` 结束，responses 流必须以 `response.completed` / `response.incomplete` 结束；缺少终止标记、或收到 `response.failed`、或 chat 返回 `content_filter` / `insufficient_system_resource` 等失败 finish reason 时，解析器抛 `LLMError`，而不是把半截文本当成完整答案。CLI 因此返回退出码 1，并打印可读错误。

## 工具循环

`Harness.run_turn()` 的一轮：

1. 追加用户消息；
2. 请求一次流式补全，同时收集文本、思考片段和工具调用；
3. 把 assistant 消息（含工具调用）写回历史；
4. 没有工具调用 → 本轮结束，返回自然语言；
5. 有工具调用 → 逐个执行 `execute_tool()`，把观测 JSON 作为 `tool` 消息写回历史，回到第 2 步；
6. 服务端因 `max_tokens` 截断且带了工具调用 → 不执行、不写入历史，直接提示"输出被截断"结束本轮；若截断发生在纯文本回答上，则在答案末尾追加"回答可能被截断"提示；
7. 超过 `max_steps`（默认 8）→ 输出受控提示并停止。

`execute_tool()` 的约束：参数 JSON 非法、工具名未知、工具抛出的 `ImageInspectionError` / `RasterInspectionError`，一律翻译成 `{"ok": false, "error": "..."}` 观测写回模型，而不是让异常炸掉终端。这样模型有机会在下一轮解释失败或换路径重试。

观测结果超过 6000 字符时保留头 4000 + 尾 1500，并插入"省略 N 个字符"的显式标记：既不让超大结果淹没上下文，也保留可诊断的尾部信息。

## 配置与凭据

`resolve_config()` 的优先级：CLI 参数 > 进程环境变量（`RS_AGENT_*`，其次 `DEEPSEEK_*`）> 本地 `.env` > 默认值（`https://api.deepseek.com`、`deepseek-flash`、`chat`）。选中配置档案时，档案字段在 CLI 参数之后、环境变量之前生效（见「配置档案」）。

- `--base-url` / `RS_AGENT_BASE_URL` / `DEEPSEEK_BASE_URL`；
- `--api-key` / `RS_AGENT_API_KEY` / `DEEPSEEK_API_KEY`；
- `--model` / `RS_AGENT_MODEL`；
- `--api chat|responses` / `RS_AGENT_API`；
- `--thinking enabled|disabled` / `RS_AGENT_THINKING`；
- `--reasoning-effort low|high|max` / `RS_AGENT_REASONING_EFFORT`（只写强度不写开关时自动启用思考）。

本地 `.env` 由 `load_env_file()` 解析（`KEY=VALUE`、`#` 注释、可选的 `export` 前缀、成对引号；文件不存在时返回空映射）。默认读取当前目录的 `.env`，可用 `--env-file` 指定；仓库只提交 [`.env.example`](../.env.example)，`.env` 在 `.gitignore` 中。`resolve_config()` 优先使用显式传入的 `env` 映射，便于测试完全隔离环境。

凭据不打印：启动横幅只显示 `sk-****` 与来源（命令行 / 档案 / 环境变量 / `.env`）；缺 key 时在首次请求前抛出可读错误（退出码 1）。

交互式会话启动时，`_interactive_menu()` 先让用户选择配置档案（存在档案时），再依次询问协议、模型 id（含自定义）、思考强度，并把结果写回 CLI 参数后再解析一次配置；菜单默认值来自 `.env`/环境变量/参数/档案，选择只对当前会话生效（要固定就写进 `.env` 或保存为配置档案）。一次性提问与 `--no-menu` 不弹菜单，便于脚本与管道使用。

## 配置档案（多套配置切换）

`profiles.py` 提供命名配置档案的持久化，支撑"保留原有配置、按需切换"：

- **模型**：`Profile`（名称 + `base_url` / `model` / `api` / `thinking` / `reasoning_effort`，以及可选的 `api_key_env`、`api_key`）；
- **存储**：`ProfileStore` 读写本地 JSON（默认 `.rs_agent_profiles.json`，已 gitignore），结构为 `{"version": 1, "last_profile": ..., "profiles": {...}}`；
- **原子写**：先写同目录 `<name>.<pid>.tmp`，再 `os.replace` 覆盖，避免半写文件；
- **密钥**：默认只存 `api_key_env`（环境变量名），`/save` 从不落明文 key；`api_key` 字段仅用于手工配置本机专用档案；
- **记忆**：`last_profile` 随档案一起持久化，下次启动自动沿用；
- **切换**：`/use <name>` 重建 client 但复用 `Harness.messages`，对话上下文不丢；
- **优先级**：CLI 参数 > 档案 > 环境变量/`.env` > 默认值。

斜杠命令解析是 CLI 层的薄封装（对应 DSH `parseCommand` + 命令注册表的最小版）：`/help`、`/profiles`、`/config`、`/use`、`/save`、`/delete`、`/exit`。启动参数 `--profile` 指定档案，`--no-profile` 忽略档案，`--profiles-file` 换存储路径。

档案文件损坏或字段非法时抛 `ProfileError`，CLI 打印可读的"配置错误"并退出码 2，而不是栈回溯。

## 思考模式的处理

DeepSeek 默认开启思考模式，且 chat 方言在带 `tools` 时要求每轮回传 `reasoning_content`，否则返回 400。MVP 的选择是默认 `thinking=disabled`（可用 `RS_AGENT_THINKING` / `.env` 覆盖）：请求体显式带 `{"thinking": {"type": "disabled"}}`（chat）或 `{"reasoning": {"effort": "none"}}`（responses），让演示稳定、输出可预期。需要思考能力时用 `--thinking enabled --reasoning-effort low|high|max` 或对应环境变量；Harness 会把 `reasoning_content` 存进 assistant 消息以便正确回传。

## 测试策略

- 网络层可替换：`LLMClient` 接受任意实现 `open()` 的 transport，测试用假 transport 注入构造好的 SSE 行；
- 请求构建与 SSE 解析是纯函数，直接断言请求体与事件序列；
- 工具循环用脚本化的假 `LLMClient`（按轮返回事件），验证"工具调用 → 观测回填 → 自然语言"的顺序与历史结构；
- 真实 API 只用于端到端冒烟，不作为单元测试前提。

## 与 DSH 参考实现的对应

调研笔记见 [`docs/dsh-minimal-harness-notes.md`](dsh-minimal-harness-notes.md)。本 MVP 借鉴了其中几条做法，但按单工具演示的规模做了裁剪：

| DSH 的做法 | 本 MVP 的对应 |
| --- | --- |
| 方言无关的内部 chunk 协议 + 薄适配器（`dsh-llm-pi-ai` 路由 openai-completions / openai-responses） | 内部 `TextDelta/ReasoningDelta/ToolCall/Done` 事件 + 两个 SSE 解析器，loop 不感知方言 |
| 工具参数在内部恒为原始 JSON 字符串，执行前再校验 | `ToolCall.arguments` 保持字符串，`execute_tool()` 解析并兜底 |
| 工具结果 head/tail 截断 + 省略脚注（output-retention / spill-policy） | `_truncate()`：6000 字符上限，头 4000 + 尾 1500 + 省略标记 |
| `max-tokens` 时丢弃不可安全执行的 tool-call | `finish_reason in {length, max_tokens, incomplete}` 且带工具调用时不执行 |
| 系统提示词 = 有序 section + persona + 工具 schema 一次装配 | MVP 简化为单段 `SYSTEM_PROMPT` + 显式 `TOOL_SPECS` |
| 密钥只以环境变量引用形式进入配置 | `RS_AGENT_API_KEY` / `DEEPSEEK_API_KEY`，配置文件不含密钥 |
| profile 目录 + `settings.yaml` + `apiKeyEnv` 引用 + 原子写 | `ProfileStore`：`.rs_agent_profiles.json` 命名档案、`api_key_env` 引用、同目录临时文件 + `os.replace` |

## 刻意不做的事

- 不引入 OpenAI SDK、HTTP 框架或 Agent 框架；
- 不做 TUI、不做多 Agent 编排、不做会话持久化（配置档案是唯一的落盘状态）；
- 不做流式工具调用的并行执行（并行调用按顺序逐个执行，保持可观察性）；
- 不做重试/退避/断线续传，失败直接给出可读错误；
- 不把工具结果做向量化或摘要，直接以 JSON 字符串回填。
