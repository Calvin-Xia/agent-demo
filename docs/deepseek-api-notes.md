# DeepSeek API 接口笔记（Chat Completions + Responses API）

> 用途：直接照着写代码的接口速查。所有结论来自官方英文文档线上内容，抓取时间 **2026-10-20**（Asia/Shanghai）。
> 官方文档在 2026 年有过模型改名（`deepseek-chat` / `deepseek-reasoner` 已不在文档中），本文以线上当前内容为准，不采用训练记忆。
> 状态：仅文档，不含实现代码。

来源（下文用简称引用）：

| 简称 | URL |
| --- | --- |
| [chat-api] | https://api-docs.deepseek.com/api/create-chat-completion |
| [resp-api] | https://api-docs.deepseek.com/api/create-response |
| [resp-guide] | https://api-docs.deepseek.com/guides/responses_api |
| [tool-guide] | https://api-docs.deepseek.com/guides/tool_calls |
| [thinking-guide] | https://api-docs.deepseek.com/guides/thinking_mode |
| [multiround-guide] | https://api-docs.deepseek.com/guides/multi_round_chat |
| [quickstart] | https://api-docs.deepseek.com/ |
| [pricing] | https://api-docs.deepseek.com/quick_start/pricing |
| [errors] | https://api-docs.deepseek.com/quick_start/error_codes |
| [ratelimit] | https://api-docs.deepseek.com/quick_start/rate_limit |
| [models-api] | https://api-docs.deepseek.com/api/list-models |
| [codex-guide] | https://api-docs.deepseek.com/quick_start/agent_integrations/codex |
| [updates] | https://api-docs.deepseek.com/updates |

---

## 1. 鉴权、base URL、模型

### 1.1 端点与鉴权

- OpenAI 风格 base URL：`https://api.deepseek.com`
- Anthropic 风格 base URL：`https://api.deepseek.com/anthropic`（本文不涉及）
- Chat Completions：`POST {base_url}/chat/completions`
- Responses：`POST {base_url}/responses`
- 模型列表：`GET {base_url}/models`
- 鉴权头：`Authorization: Bearer <DEEPSEEK_API_KEY>`，同时需要 `Content-Type: application/json`
  （来源：[quickstart] curl 示例）

```bash
curl https://api.deepseek.com/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${DEEPSEEK_API_KEY}" \
  -d '{"model":"deepseek-flash","messages":[{"role":"user","content":"Hello!"}],"stream":false}'
```

### 1.2 `/v1` 前缀是否接受

- 官方文档**从头到尾没有出现 `/v1`**：所有示例的 base URL 都是 `https://api.deepseek.com`，路径直接是 `/chat/completions`、`/responses`（[quickstart]、[chat-api]、[resp-guide]、[codex-guide]）。
- 本次未能用未鉴权请求验证 `/v1` 是否路由：`GET https://api.deepseek.com/models`、`GET https://api.deepseek.com/v1/models`、以及一个刻意不存在的路径都返回 `401`，说明网关在路由匹配之前就做了鉴权，无法据此区分路径是否存在。
- 实现建议：**按官方 base URL 原样配置**，不要自己拼 `/v1`。多数 OpenAI SDK 只需要 `base_url`，不会强制追加 `/v1`；若某个客户端固定拼 `/v1`，必须实测后再上线。官方 Codex 集成配置可以直接对照：`base_url = "https://api.deepseek.com/"`、`wire_api = "responses"`（[codex-guide]）。

### 1.3 当前可用模型

`model` 取值只有两个（[chat-api]、[resp-api]、[pricing]）：

| 模型 id | 实际模型版本 | 上下文长度 | 最大输出 | 思考模式 | 视觉 | Responses API | Tool Calls |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `deepseek-flash` | DeepSeek-V4.1-Flash-1017 | 1M | 最大 384K | 非思考 + 思考（默认思考） | ✓ | ✓ | ✓ |
| `deepseek-v4-pro` | DeepSeek-V4-Pro-0813 | 1M | 最大 384K | 非思考 + 思考（默认思考） | ✗ | ✓ | ✓ |

- `deepseek-flash` 是当前主推名字；旧名 `deepseek-v4-flash`、`deepseek-v4-flash-vision-exp` **仍被接受**，但对应模型已下线，请求会路由到 DeepSeek-V4.1-Flash-1017，按 Flash 价格计费（[pricing] 脚注 1、[updates] 2026-09-10）。
- 并发上限（账号级）：`deepseek-flash` 2500，`deepseek-v4-pro` 500（[pricing]、[ratelimit]）。
- `GET /models` 会返回每个模型的 `context_window`、`max_output_tokens`（即 `max_tokens` 允许的最大值）、`input_modalities`、`output_modalities`、`effort.supported_levels` / `default_level`、`api_capabilities` 等元数据，代码里可以启动时拉一次做校验，不要写死（[models-api]）。

### 1.4 思考模式开关（两种协议都能用）

默认开启思考模式。三种等价写法：

- Chat：`"thinking": {"type": "enabled" | "disabled"}`；`type` 默认 `enabled`（[chat-api]）。
- Chat / Responses：`reasoning_effort`（Chat 顶层字段）/ `reasoning.effort`（Responses）= `none | low | high | max`。`none` 关闭思考，`low/high/max` 开启，默认 `high`。兼容映射：`minimal → low`，`medium / xhigh → high`（[chat-api]、[resp-api]、[thinking-guide]）。
- Responses 不传 `reasoning` 时使用模型默认行为（默认开启）（[resp-api]）。

注意：思考模式下 `temperature` 无效（不报错但被忽略），`top_p` 行为随模型变化（见 §2.1）。

---

## 2. Chat Completions：`POST /chat/completions`

来源：[chat-api]、[tool-guide]、[thinking-guide]

### 2.1 请求字段（顶层）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `model` | string | 是 | `deepseek-flash` / `deepseek-v4-pro` |
| `messages` | object[] | 是 | 至少 1 条；角色 system / user / assistant / tool |
| `thinking` | object | 否 | `{"type": "enabled" \| "disabled"}`，默认 `enabled` |
| `reasoning_effort` | string | 否 | `none/low/high/max`，默认 `high`（与 `thinking` 并列的顶层字段） |
| `max_tokens` | int | 否 | 1–393216（384K）。不填默认：非思考 8K、思考 64K、`reasoning_effort=max` 时 128K |
| `response_format` | object | 否 | `{"type":"text"}`（默认）或 `{"type":"json_object"}`；用 JSON 模式必须在 system/user 里明确要求输出 JSON |
| `stop` | string \| string[] | 否 | 最多 16 个停止序列 |
| `stream` | bool | 否 | true 时返回 SSE，最终以 `data: [DONE]` 结束 |
| `stream_options` | object | 否 | `{"include_usage": true}`；必须与 `stream: true` 同时使用，否则 400 |
| `temperature` | number | 否 | 0–2，默认 1；**思考模式下无效** |
| `top_p` | number | 否 | 默认 1。`deepseek-flash`：仅思考模式生效，有效区间 0.95–1.0（低于 0.95 按 0.95）；非思考固定 1.0，传入被忽略 |
| `tools` | object[] | 否 | 仅支持 `function` 类型，见 §2.3 |
| `tool_choice` | string \| object | 否 | `none` / `auto` / `required` / `{"type":"function","function":{"name":"..."}}`。无 tools 时默认 `none`，有 tools 时默认 `auto` |
| `logprobs` / `top_logprobs` | bool / int | 否 | `top_logprobs` 0–20，且 `logprobs=true` 才可用 |
| `user_id` | string | 否 | `[a-zA-Z0-9\-_]+`，≤512；用于内容安全、KVCache 隔离、调度隔离（注意是 `user_id`，不是 OpenAI 的 `user`） |
| `frequency_penalty` / `presence_penalty` | — | — | **已废弃**，传了也不生效 |

### 2.2 messages 四类角色

- `system`：`{"role":"system","content":"..."}`，`content` 必填字符串。
- `user`：`content` 可以是字符串，也可以是内容块数组（`{"type":"text","text":...}`、`{"type":"image_url","image_url":{"url":...,"detail":"low|high|original|auto"}}`、`{"type":"file","file_id":... 或 file_data:...}`）。图片体积/格式限制见 Vision 指南。
- `assistant`：`{"role":"assistant","content": string|null}`；工具调用时还会带 `tool_calls`；思考模式下带 `reasoning_content`（见 §2.6）。`content` 字段在 schema 里是 required 但 nullable，因此回传历史时写 `"content": null` 最稳。
- `tool`：`{"role":"tool","tool_call_id":"<assistant 中同一个 tool_call.id>","content":"<工具结果字符串或内容块数组>"}`。`tool_call_id` 必填。

### 2.3 tools / tool_choice / strict

```json
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get weather of a location, the user should supply a location first.",
        "parameters": {
          "type": "object",
          "properties": {"location": {"type": "string"}},
          "required": ["location"]
        }
      }
    }
  ],
  "tool_choice": "auto"
}
```

- `function.name`：只能含 `a-z A-Z 0-9 _ -`，≤128，且所有工具内唯一。省略 `parameters` 表示无参函数。
- 指定某个工具：`{"type": "function", "function": {"name": "get_weather"}}`。
- **思考模式下 `tool_choice: "required"` 和指定具体工具都不支持，会返回 400**；要用就先关思考（`thinking.type=disabled` 或 `reasoning_effort=none`）（[chat-api]）。
- `strict` 模式（Beta）：必须 1) 使用 `base_url="https://api.deepseek.com/beta"`；2) 每个 function 设 `"strict": true`；3) schema 需符合限制——所有 object 的**所有属性都要进 `required`**、`additionalProperties: false`；支持 object/string/number/integer/boolean/array/enum/anyOf，不支持 `minLength`/`maxLength`/`minItems`/`maxItems`；支持 `$ref`/`$def`。服务端会校验 schema，不合规直接报错。（[tool-guide]）
- 模型不保证 `arguments` 是合法 JSON，也可能幻觉出 schema 外的参数，**必须在自己代码里校验**（[chat-api]）。

### 2.4 非流式响应结构

```json
{
  "id": "1f633d8bfc032625086f14113c411638",
  "object": "chat.completion",
  "created": 1718345013,
  "model": "deepseek-flash",
  "system_fingerprint": "fp_a49d71b8a1",
  "choices": [
    {
      "index": 0,
      "finish_reason": "tool_calls",
      "message": {
        "role": "assistant",
        "content": null,
        "reasoning_content": null,
        "tool_calls": [
          {
            "id": "call_0",
            "type": "function",
            "function": {"name": "get_weather", "arguments": "{\"location\":\"Hangzhou\"}"}
          }
        ]
      }
    }
  ],
  "usage": {
    "prompt_tokens": 17,
    "completion_tokens": 9,
    "total_tokens": 26,
    "prompt_tokens_details": {"cached_tokens": 0},
    "prompt_cache_hit_tokens": 0,
    "prompt_cache_miss_tokens": 17,
    "completion_tokens_details": {"reasoning_tokens": 0}
  }
}
```

- `finish_reason` 枚举：`stop` / `length` / `content_filter` / `tool_calls` / `insufficient_system_resource` / `aborted`。
- 思考模式下 CoT 走 `message.reasoning_content`（与 `content` 同级）。
- `usage` 字段：`completion_tokens`、`prompt_tokens`（= hit + miss）、`total_tokens`、`prompt_tokens_details.cached_tokens`、`prompt_cache_hit_tokens`、`prompt_cache_miss_tokens`、`completion_tokens_details.reasoning_tokens`。

### 2.5 流式（SSE）增量格式

关键事实（[chat-api]）：

1. 只有 `data:` 行，**没有 `event:` 字段**；每个 chunk 是 `chat.completion.chunk`，所有 chunk 的 `id` / `created` 相同。
2. 流以 `data: [DONE]` 结束（必须单独判断这一行，它不是 JSON）。
3. `usage` **只出现在 `[DONE]` 前的最后一个 chunk**：该 chunk 的 `choices[0].delta` 没有新内容、`finish_reason` 非 null。**不会单独发一个 usage-only chunk**。
   - 若 `stream_options.include_usage=true`：所有 chunk 都带 `usage` 字段，前面为 `null`，最后一个为完整统计；不传时前面 chunk 完全没有 `usage` 键。
4. 每个 chunk 的 `choices[0].delta` 可能出现：`role`（第一个 chunk，值为 `assistant`，常伴随空 `content`）、`content`（文本增量）、`reasoning_content`（思考增量）、`tool_calls`（工具调用增量）。
5. 官方文档明确写明了 tool_calls 的增量规则：**每个 tool call 的第一个 chunk 带 `id`、`type` 和 `function` 字段，后续 chunk 只带 `function.arguments` 片段**；用 `index` 区分不同（并行）工具调用。
6. 网络层可能插入 SSE 注释 `: keep-alive`（[ratelimit]），解析时必须跳过以 `:` 开头的行；非流式请求则可能收到空行。若请求发出后 10 分钟仍未开始推理，服务端会关闭连接。

真实 chunk 形态（节选官方示例，[chat-api]）：

```
data: {"id":"1f633d8bfc032625086f14113c411638","choices":[{"index":0,"delta":{"content":"","role":"assistant"},"finish_reason":null,"logprobs":null}],"created":1718345013,"model":"deepseek-flash","system_fingerprint":"fp_a49d71b8a1","object":"chat.completion.chunk"}
data: {"choices":[{"delta":{"content":"Hello","role":"assistant"},"finish_reason":null,"index":0,"logprobs":null}],...}
...
data: {"choices":[{"delta":{"content":"","role":null},"finish_reason":"stop","index":0,"logprobs":null}],...,"usage":{"completion_tokens":9,"prompt_tokens":17,"total_tokens":26,"prompt_tokens_details":{"cached_tokens":0},"prompt_cache_hit_tokens":0,"prompt_cache_miss_tokens":17}}
data: [DONE]
```

工具调用的拼接规则（实现要点）：

```
按 delta.tool_calls[i].index 建 map：
  - 第一次见到某个 index：记下 id / type / function.name
  - 之后：把 function.arguments 字符串不断 += 到同一个 index 上
全部 chunk 结束后：
  - arguments 是完整 JSON 字符串，json.loads 后再调用工具（并做 schema 校验）
  - 多个 index = 同一条 assistant 消息里的并行工具调用，每个都要各自回一条 role=tool 消息
```

`reasoning_content` 拼接：如果本轮是思考模式，把每个 chunk 的 `delta.reasoning_content` 依次 `+=` 拼成完整 CoT，下一轮请求要原样放回 assistant 消息（见 §2.6）。

### 2.6 多轮工具调用时的消息回传（含思考模式硬性要求）

一次工具调用往返（非思考模式，[tool-guide]）：

```json
[
  {"role": "system", "content": "You are a helpful assistant."},
  {"role": "user", "content": "How's the weather in Hangzhou, Zhejiang?"},
  {"role": "assistant", "content": null,
   "tool_calls": [{"id": "call_0", "type": "function",
                   "function": {"name": "get_weather", "arguments": "{\"location\":\"Hangzhou\"}"}}]},
  {"role": "tool", "tool_call_id": "call_0", "content": "24℃"}
]
```

**思考模式的硬性约束**（[thinking-guide]，很容易踩）：

- 请求**带 `tools`**：所有历史轮次的 `reasoning_content` **必须完整回传**，包括没有发生工具调用的那些轮次；不回传会直接 **400**。
- 请求**不带 `tools`**：`reasoning_content` 不必回传，传了也会被忽略、不进入上下文。
- 由于 `reasoning_content` 在流式里是分片来的，你必须自己拼接后写回 assistant 消息，不能只存 `content`。

另外（[tool-guide] 的格式差异说明）：

- Chat Completions **不支持**在对话中段插入"模型没生成过"的工具调用（也不支持中段系统消息之外的动态注入）；需要这种能力要用 Responses API 或 Anthropic API。
- Chat Completions **支持**在对话中段插入 `system` 消息。

---

## 3. Responses API：`POST /responses`

来源：[resp-api]、[resp-guide]、[tool-guide]

### 3.1 顶层请求字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `model` | string | 是 | `deepseek-flash` / `deepseek-v4-pro` |
| `input` | string \| object[] | 否 | 字符串等价于一条 user 消息；或 input items 列表。**`input` 与 `instructions` 至少要给一个** |
| `instructions` | string | 否 | 被**插入为上下文中第一条 system 消息** |
| `reasoning` | object | 否 | `{"effort": "none"｜"low"｜"high"｜"max"}`；`summary` 可传但不生成摘要 |
| `max_output_tokens` | int | 否 | **包含思考 token** 在内的输出上限（对应 Chat 的 `max_tokens`） |
| `stream` | bool | 否 | 语义化 SSE，见 §3.5；**没有 `[DONE]`** |
| `temperature` | number | 否 | 0–2，默认 1；思考模式无效 |
| `top_p` | number | 否 | `deepseek-flash`：仅思考模式生效（下界 0.95），非思考固定 1.0；`deepseek-v4-pro`：思考模式忽略（有 tools 时固定 0.95，否则 1.0），非思考生效 |
| `text.format` | object | 否 | `{"type":"text"}` / `{"type":"json_object"}` / `{"type":"json_schema","name":...,"schema":...}` |
| `tools` | object[] | 否 | `function` 支持；`custom` 仅 `apply_patch`；`web_search` 仅 `deepseek-v4-pro`；其他内置类型被忽略 |
| `tool_choice` | string \| object | 否 | `none` / `auto`（默认）/ `required` / `{"type":"function","name":"get_weather"}` |
| `top_logprobs` | int | 否 | 0–20 |
| `user` | string | 否 | `[a-zA-Z0-9\-_]+`，≤512（注意：Responses 用 `user`，Chat 用 `user_id`） |

**明确不支持 / 被静默忽略的顶层参数**（[resp-guide]，重要：**不报错，直接忽略**）：

`previous_response_id`（无状态，不支持）、`conversation`、`store`（响应里始终 `store: false`）、`background`、`metadata`、`include`、`prompt`、`parallel_tool_calls`（并行工具调用**始终开启**）、`max_tool_calls`、`truncation`（超上下文直接 400）、`service_tier`、`safety_identifier`、`prompt_cache_key` / `prompt_cache_retention`、`context_management`、`stream_options`。

实现提醒：因为是"静默忽略"，拼错参数名或沿用 Chat 的 `stream_options` 都不会报错，只会安静地不生效——务必对照上表自查。

### 3.2 input items

支持的类型：`message`、`function_call`、`function_call_output`、`custom_tool_call`、`custom_tool_call_output`、`reasoning`、`web_search_call`；**其他类型被忽略**。

| item | 关键字段 |
| --- | --- |
| `message` | `role`：`user` / `assistant` / `system` / `developer`（`developer` 当 `user` 处理）。`content` 可字符串，或 `input_text` / `output_text` / `input_image` 内容块数组。`type` 在有 `role` 时可省略 |
| `function_call` | `type`、`call_id`、`name`、`arguments`（JSON 字符串） |
| `function_call_output` | `type`、`call_id`、`output`（字符串，或 `input_text` / `input_image` 内容块数组——工具返回图片就用这个） |
| `reasoning` | `content` 为 `reasoning_text` 内容块数组；纯文本 `content` 会被合并进相邻 assistant 消息；`summary` / `encrypted_content` 不支持 |

`call_id` 约束（[resp-api]）：非空、唯一，且**每个 `function_call` 必须有配对的 `function_call_output`**。

关于 thinking：Chat 侧"带 tools 时必须回传 reasoning_content"是明文规定；Responses 侧文档只说 `reasoning` item 受支持、纯文本内容会合并进相邻 assistant 消息，**没有写明必须回传**。回传时只保留纯文本 `content`（`reasoning_text`），不要带 `summary` / `encrypted_content`。

### 3.3 tools（与 Chat 的关键差别：**没有嵌套 `function`**）

```json
{
  "tools": [
    {
      "type": "function",
      "name": "get_weather",
      "description": "Get weather of a location.",
      "parameters": {
        "type": "object",
        "properties": {"location": {"type": "string"}},
        "required": ["location"]
      }
    }
  ],
  "tool_choice": "auto"
}
```

- `name` 规则：非空、≤128、`^[a-zA-Z0-9_-]+$`、所有工具内唯一。
- 指定具体工具：`{"type": "function", "name": "get_weather"}`（**没有** Chat 那种 `{"function": {"name": ...}}` 包一层）。
- Responses 的 tool schema 里**没有** `strict` 字段；`strict` 模式目前只在 Chat Completions（`/beta`）文档中定义（[tool-guide]、[resp-api]）。
- `custom` 只接受 `{"type":"custom","name":"apply_patch"}`（Codex 兼容），其他名字 400；`web_search` 仅 `deepseek-v4-pro`，`deepseek-flash` 下被忽略。

### 3.4 非流式响应对象

```json
{
  "id": "24778070-1c36-4ae0-a4bd-870afc7fc13e",
  "object": "response",
  "created_at": 1753000000,
  "status": "completed",
  "model": "deepseek-flash",
  "output": [
    {
      "type": "reasoning",
      "id": "rs_1",
      "status": "completed",
      "content": [{"type": "reasoning_text", "text": "The user greets me. I should reply politely."}],
      "summary": []
    },
    {
      "type": "message",
      "id": "msg_1",
      "status": "completed",
      "role": "assistant",
      "content": [{"type": "output_text", "text": "Hello! How can I help you today?", "annotations": []}]
    }
  ],
  "usage": {
    "input_tokens": 22,
    "input_tokens_details": {"cached_tokens": 0},
    "output_tokens": 29,
    "output_tokens_details": {"reasoning_tokens": 27},
    "total_tokens": 51
  },
  "store": false,
  "parallel_tool_calls": true,
  "previous_response_id": null,
  "error": null,
  "incomplete_details": null
}
```

- `status`：`in_progress` / `completed` / `incomplete` / `failed`；`incomplete_details.reason` 为 `max_output_tokens` 或 `content_filter`；`error` 为 `{code, message}`。
- `output[]` item 类型：`message` / `reasoning` / `function_call` / `custom_tool_call` / `web_search_call` / `tool_search_call`。
- 工具调用 item：`{"type":"function_call","id":"fc_1","call_id":"call_1","name":"get_weather","arguments":"{\"location\":\"Hangzhou\"}","status":"completed"}`。
- `usage`：`input_tokens`（`input_tokens_details.cached_tokens` 为命中上下文缓存的 token 数）、`output_tokens`（`output_tokens_details.reasoning_tokens` 为 CoT token 数）、`total_tokens`。
- 官方示例里 `response.output_text` 是 **OpenAI SDK 的便捷聚合属性**，不是必然存在于原始 JSON；自己解析时要遍历 `output[]` 里 `type=="message"` 的 `content[].text`。

### 3.5 流式语义化 SSE（**没有 `[DONE]`**）

- 每个事件都有 `event:` 字段（事件类型）+ `data:` JSON；JSON 内还有 `type`（同事件名）和**单调递增的 `sequence_number`**。
- **流的结束条件是收到 `response.completed` / `response.incomplete` / `response.failed` 三者之一，官方明确说没有 `data: [DONE]`**。收到后服务端关闭连接，之后不再有数据行。
- `response.completed` 的 `data.response` 是完整 `response` 对象，**`usage` 在这里**；`response.incomplete` 用于被截断（如到达 `max_output_tokens`）；`response.failed` 的 `response.error` 带错误详情。

完整事件表（[resp-guide]）：

| 事件 | 含义 |
| --- | --- |
| `response.created` | 第一个事件，响应已创建，状态 `in_progress` |
| `response.in_progress` | 响应生成中 |
| `response.output_item.added` / `.done` | 一个 output item 开始 / 完成（`reasoning` / `message` / `function_call` / `custom_tool_call` / `web_search_call` / `tool_search_call`） |
| `response.content_part.added` / `.done` | item 内的一个 content part 开始 / 完成 |
| `response.reasoning_text.delta` / `.done` | CoT 文本增量 / 完整 CoT |
| `response.output_text.delta` / `.done` | 输出文本增量 / 完整输出文本 |
| `response.function_call_arguments.delta` / `.done` | 工具调用参数增量 / 完整参数 |
| `response.custom_tool_call_input.delta` / `.done` | `apply_patch` 等 custom tool 的输入增量 / 完整输入 |
| `response.web_search_call.in_progress` / `.searching` / `.completed` | 服务端 `web_search` 开始 / 进行 / 完成（仅 `deepseek-v4-pro`） |
| `response.completed` | 正常结束，携带完整 `response`（含 `usage`） |
| `response.incomplete` | 截断结束，携带完整 `response` |
| `response.failed` | 失败结束，携带带 `error` 的完整 `response` |

官方流式示例（[resp-api]）：

```
event: response.created
data: {"type": "response.created", "sequence_number": 0, "response": {"id": "...", "object": "response", "status": "in_progress", ...}}
event: response.output_item.added
data: {"type": "response.output_item.added", "sequence_number": 2, "output_index": 0, "item": {"type": "reasoning", ...}}
event: response.reasoning_text.delta
data: {"type": "response.reasoning_text.delta", "sequence_number": 4, "item_id": "rs_1", "output_index": 0, "content_index": 0, "delta": "The user"}
event: response.output_item.added
data: {"type": "response.output_item.added", "sequence_number": 9, "output_index": 1, "item": {"type": "message", "role": "assistant", ...}}
event: response.output_text.delta
data: {"type": "response.output_text.delta", "sequence_number": 11, "item_id": "msg_1", "output_index": 1, "content_index": 0, "delta": "Hello"}
event: response.completed
data: {"type": "response.completed", "sequence_number": 20, "response": {"id": "...", "object": "response", "status": "completed", "usage": {...}, ...}}
```

解析要点：

- 文本：拼 `response.output_text.delta.delta`；CoT：拼 `response.reasoning_text.delta.delta`。
- 工具参数：拼 `response.function_call_arguments.delta.delta`（用 `item_id` 或 `output_index` 归组），或直接等 `response.function_call_arguments.done` 拿完整参数；`response.output_item.done` 里也会带完整 item（含 `call_id` / `name` / `arguments`）。
- 拿工具调用三要素（`call_id`、`name`、`arguments`）最稳的方式：从 `response.output_item.done`（item.type == `function_call`）取，或从最终 `response.completed.response.output[]` 取。
- 结束判断只看这三个终态事件，**不要**等 `[DONE]`，也**不要**以连接关闭作为唯一判据（连接可能异常中断，此时应视为错误）。
- SSE 注释行（`:` 开头）按规范跳过；Chat 侧文档明确写了 `: keep-alive`，Responses 侧按同一套 SSE 规则防御性处理。

### 3.6 多轮工具调用：怎么把 function_call / function_call_output 回传

Responses 是**无状态**的：每轮都要把**完整对话历史**放进 `input`；`previous_response_id` 不支持（[resp-guide]、[resp-api]）。

第二轮请求示例（接 §4 的天气场景）：

```json
{
  "model": "deepseek-flash",
  "instructions": "You are a helpful assistant. Use tools when needed.",
  "reasoning": {"effort": "none"},
  "input": [
    {"role": "user", "content": "杭州今天天气怎么样？"},
    {"type": "function_call", "call_id": "call_1", "name": "get_weather", "arguments": "{\"location\":\"杭州\"}"},
    {"type": "function_call_output", "call_id": "call_1", "output": "Cloudy 7~13°C"}
  ],
  "tools": [
    {"type": "function", "name": "get_weather", "description": "Get weather of a location.",
     "parameters": {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}}
  ],
  "tool_choice": "auto",
  "stream": true
}
```

规则与坑：

- `function_call` 与 `function_call_output` 的 `call_id` 必须成对一致；每个 `function_call` 都要有对应输出，否则报错。
- 上一轮响应里的 `function_call` 会被服务端合并进相邻的 assistant 消息；历史里的 `function_call` + `function_call_output` 顺序要成对出现（call 在前、output 紧随其后）。
- 官方示例里 `function_call_output.output` 支持内容块数组，因此工具返回图片可以直接塞 `input_image`（`deepseek-flash` 会真实处理图片）。
- 可选：把上一轮的 `message`（assistant 文本）和 `reasoning` item 也回传，保持上下文完整；`reasoning` 只保留纯文本 `content`。
- 注意 "每一个 function_call 必须有 function_call_output" 也意味着：**不要把模型刚生成的、还没执行的 function_call 放进历史**——必须等执行完拿到结果再一起发。
- Chat 侧不支持的"中段插入工具调用"在 Responses 侧是支持的（[tool-guide]），做 Codex 类 harness 时这是选 Responses 的主要理由之一。

---

## 4. 对照表：同一场景两边的映射

场景：`system` 提示 + 用户问杭州天气 + 一个 `get_weather(location)` 工具 + 第一轮回合触发工具调用 + 第二轮把结果喂回去拿最终自然语言回答。为对齐，示例里两边都**关闭思考**（`thinking.type=disabled` / `reasoning.effort=none`）；开启思考时 Chat 还要额外回传 `reasoning_content`（§2.6）。

### 4.1 概念映射

| 概念 | Chat Completions | Responses API |
| --- | --- | --- |
| 端点 | `POST /chat/completions` | `POST /responses` |
| 系统提示 | `messages[0] = {"role":"system","content":...}` | `instructions`（或 input 里的 system/developer message） |
| 用户输入 | `messages[] = {"role":"user","content":...}` | `input[i] = {"role":"user","content":...}`（或 `input` 直接给字符串） |
| assistant 文本 | `{"role":"assistant","content":"..."}` | output item `{"type":"message","role":"assistant","content":[{"type":"output_text","text":"..."}]}` |
| 工具定义 | `{"type":"function","function":{"name","description","parameters"}}` | `{"type":"function","name","description","parameters"}`（**扁平，无 function 包层**） |
| 指定工具 | `tool_choice={"type":"function","function":{"name":"x"}}` | `tool_choice={"type":"function","name":"x"}` |
| 模型发起的工具调用 | `message.tool_calls[] = {"id","type","function":{"name","arguments"}}` | output item `{"type":"function_call","id","call_id","name","arguments"}` |
| 工具结果回传 | `{"role":"tool","tool_call_id":"<id>","content":...}`（每个 call 一条） | input item `{"type":"function_call_output","call_id":"<call_id>","output":...}` |
| CoT | `message.reasoning_content`（字符串，流式拼起来） | output item `{"type":"reasoning","content":[{"type":"reasoning_text","text":...}]}` |
| 思考开关 | `thinking:{"type":"enabled"\|"disabled"}` 或 `reasoning_effort` | `reasoning:{"effort":"none"\|"low"\|"high"\|"max"}` |
| 输出上限 | `max_tokens`（默认非思考 8K / 思考 64K） | `max_output_tokens`（含思考 token） |
| 结束原因 | `choices[0].finish_reason`（`stop`/`length`/`content_filter`/`tool_calls`/…） | `status`（`completed`/`incomplete`/`failed`）+ `incomplete_details.reason` |
| 流式终止 | `data: [DONE]` | `response.completed` / `response.incomplete` / `response.failed`（**无 [DONE]**） |
| 流式事件形状 | 只有 `data:`，chunk 带 `choices[0].delta` | `event:` + `data:`，`data.type` + `sequence_number` |
| 用法统计 | `usage.prompt_tokens` / `completion_tokens` / `total_tokens`，`prompt_cache_hit_tokens`，`completion_tokens_details.reasoning_tokens` | `usage.input_tokens` / `output_tokens` / `total_tokens`，`input_tokens_details.cached_tokens`，`output_tokens_details.reasoning_tokens` |
| 会话状态 | 无状态，自己带全量 messages | 无状态，自己带全量 input（无 previous_response_id） |
| 并行工具调用 | 一条 assistant 消息里多个 `tool_calls`（按 `index` 分片） | 始终开启（`parallel_tool_calls` 参数被忽略，回显恒为 `true`） |
| 中段插入工具调用 | 不支持 | 支持 |
| 特有字段 | `stream_options`、`user_id`、`logprobs` | `text.format`（json_schema）、`custom_tool_call`、`web_search`、`instructions` |

### 4.2 第一轮请求：两边等价最小 JSON

Chat Completions：

```json
{
  "model": "deepseek-flash",
  "thinking": {"type": "disabled"},
  "max_tokens": 1024,
  "messages": [
    {"role": "system", "content": "You are a helpful assistant. Use tools when needed."},
    {"role": "user", "content": "杭州今天天气怎么样？"}
  ],
  "tools": [
    {"type": "function",
     "function": {
       "name": "get_weather",
       "description": "Get weather of a location.",
       "parameters": {"type": "object",
                      "properties": {"location": {"type": "string"}},
                      "required": ["location"]}
     }}
  ],
  "tool_choice": "auto",
  "stream": true,
  "stream_options": {"include_usage": true}
}
```

Responses：

```json
{
  "model": "deepseek-flash",
  "instructions": "You are a helpful assistant. Use tools when needed.",
  "reasoning": {"effort": "none"},
  "max_output_tokens": 1024,
  "input": [
    {"role": "user", "content": "杭州今天天气怎么样？"}
  ],
  "tools": [
    {"type": "function",
     "name": "get_weather",
     "description": "Get weather of a location.",
     "parameters": {"type": "object",
                    "properties": {"location": {"type": "string"}},
                    "required": ["location"]}}
  ],
  "tool_choice": "auto",
  "stream": true
}
```

两边的差异：Responses 没有 `stream_options`（传了被忽略），上限字段名不同，`tools` 结构不同，系统提示走 `instructions`。

### 4.3 第一轮返回后，你在本地重建出的"轮到工具执行"状态

Chat（流式拼完后等价于非流式 message）：

```json
{
  "role": "assistant",
  "content": null,
  "tool_calls": [
    {"id": "call_0", "type": "function",
     "function": {"name": "get_weather", "arguments": "{\"location\":\"杭州\"}"}}
  ]
}
```

Responses（从 `response.completed.response.output[]` 或 `output_item.done` 取）：

```json
{
  "type": "function_call",
  "id": "fc_1",
  "call_id": "call_1",
  "name": "get_weather",
  "arguments": "{\"location\":\"杭州\"}",
  "status": "completed"
}
```

执行工具 → 得到 `Cloudy 7~13°C`。

### 4.4 第二轮请求：把工具结果喂回去

Chat（**追加**两条消息，前面历史保持不动）：

```json
{
  "model": "deepseek-flash",
  "thinking": {"type": "disabled"},
  "max_tokens": 1024,
  "messages": [
    {"role": "system", "content": "You are a helpful assistant. Use tools when needed."},
    {"role": "user", "content": "杭州今天天气怎么样？"},
    {"role": "assistant", "content": null,
     "tool_calls": [{"id": "call_0", "type": "function",
                     "function": {"name": "get_weather", "arguments": "{\"location\":\"杭州\"}"}}]},
    {"role": "tool", "tool_call_id": "call_0", "content": "Cloudy 7~13°C"}
  ],
  "tools": [
    {"type": "function",
     "function": {"name": "get_weather", "description": "Get weather of a location.",
                  "parameters": {"type": "object",
                                 "properties": {"location": {"type": "string"}},
                                 "required": ["location"]}}}
  ],
  "tool_choice": "auto",
  "stream": true
}
```

Responses（**重发**完整 `input`，把 `function_call` 和 `function_call_output` 成对插入）：

```json
{
  "model": "deepseek-flash",
  "instructions": "You are a helpful assistant. Use tools when needed.",
  "reasoning": {"effort": "none"},
  "max_output_tokens": 1024,
  "input": [
    {"role": "user", "content": "杭州今天天气怎么样？"},
    {"type": "function_call", "call_id": "call_1", "name": "get_weather",
     "arguments": "{\"location\":\"杭州\"}"},
    {"type": "function_call_output", "call_id": "call_1", "output": "Cloudy 7~13°C"}
  ],
  "tools": [
    {"type": "function", "name": "get_weather", "description": "Get weather of a location.",
     "parameters": {"type": "object",
                    "properties": {"location": {"type": "string"}},
                    "required": ["location"]}}
  ],
  "tool_choice": "auto",
  "stream": true
}
```

### 4.5 流式事件对应关系

| 你关心的事情 | Chat Completions | Responses API |
| --- | --- | --- |
| 文本增量 | `choices[0].delta.content` | `response.output_text.delta` 的 `delta` |
| CoT 增量 | `choices[0].delta.reasoning_content` | `response.reasoning_text.delta` 的 `delta` |
| 工具调用开始 | 第一个带 `id`/`type`/`function.name` 的 `delta.tool_calls[i]` | `response.output_item.added`（`item.type=="function_call"`） |
| 工具参数增量 | `delta.tool_calls[i].function.arguments` 拼接 | `response.function_call_arguments.delta` 的 `delta` 拼接 |
| 工具参数完整值 | 流结束后拼接结果 | `response.function_call_arguments.done` 或 `output_item.done.item.arguments` |
| 结束 | `choices[0].finish_reason` 非 null + `data: [DONE]` | `response.completed` / `response.incomplete` / `response.failed` |
| usage | 最后一个 chunk 的 `usage` | `response.completed.response.usage` |

---

## 5. 实现注意点 / 坑（合并两边）

1. **思考默认开启**。不显式关思考时，Chat 侧带 `tools` 的 agent loop 必须每轮回传 `reasoning_content`，否则 **400**（[thinking-guide]）。最小 harness 想省事就先关：`thinking.type=disabled` 或 `reasoning_effort=none`；Responses 用 `reasoning.effort=none`。
2. **Responses 无 `previous_response_id`**，也没有 `conversation`，服务端不存会话；每轮必须重发完整 `input`（[resp-guide]）。Chat 同样无状态，每轮重发完整 `messages`（[multiround-guide]）。
3. **Responses 的 tool 定义是扁平的**：`{"type":"function","name":...}`。把 Chat 的 `{"type":"function","function":{...}}` 直接照搬过去会缺 `name`，属于必错项；反过来把 Responses 的扁平结构喂给 Chat 同样是错的。
4. **Responses 静默忽略不支持参数**：`stream_options`、`store`、`parallel_tool_calls`、`metadata`… 写了不报错也不生效（[resp-guide]）。切换协议时不要假设"没报错就是生效了"。
5. **流式结束条件完全不同**：Chat 等 `data: [DONE]`；Responses 等 `response.completed/incomplete/failed`，**永远不会有 `[DONE]`**。Responses 的连接关闭不等于成功。
6. **并行工具调用**：Responses 侧始终开启且无法关闭（`parallel_tool_calls` 被忽略，回显 `true`）；Chat 侧模型也可能一次返回多个 `tool_calls`（用 `delta.tool_calls[i].index` 区分）。两个协议都必须实现"一次返回 N 个调用 → 执行 N 次 → 回传 N 条结果"的循环，不能假设只有一个。
7. **`tool_choice` 的形状差异**：指定工具时 Chat 是 `{"type":"function","function":{"name":...}}`，Responses 是 `{"type":"function","name":...}`。
8. **Chat 思考模式下 `tool_choice=required` 或指定具体工具会 400**（[chat-api]）。要做"强制调用某工具"，先关思考。
9. **工具参数不保证合法 JSON**，模型也可能幻觉出 schema 外字段；两边都要求你自己校验（[chat-api]、[resp-api]）。Chat 的 `strict`（Beta，需 `/beta` base URL + `additionalProperties:false` + 所有属性 required）能降低风险，但**不要指望它是普通 base URL 上的默认行为**；Responses 文档未定义 `strict`。
10. **`max_tokens` / `max_output_tokens` 建议显式设置**：Chat 非思考默认只有 8K，长工具参数或长回答容易被截断（`finish_reason=length`）；Responses 的 `max_output_tokens` **包含思考 token**，思考很长的任务要给足余量。上下文 1M，超限时 Responses 直接 400（`truncation` 不支持）。
11. **`user_id` vs `user`**：Chat 用 `user_id`，Responses 用 `user`；Responses 的 `metadata`、`prompt_cache_key` 不支持，别把 Chat 的用法照搬。
12. **思考模式下 `temperature` 无效**（两边都不报错），`top_p` 的生效区间/是否被忽略要看模型和模式（§2.1、§3.1）。用默认值最省心。
13. **构建 Chat 历史时务必原样保留 `content: null` 和 `tool_calls`**：只存文本会把工具调用历史弄丢；思考模式还要把流式拼接后的 `reasoning_content` 写回。
14. **Responses 的 `call_id` 必须成对**：每个 `function_call` 都要有 `function_call_output`；否则报错。回复历史时保持 call → output 的相邻顺序。
15. **Chat 不支持中段插入工具调用**（Responses/Anthropic 支持，[tool-guide]）。如果 harness 需要注入模型没生成的工具调用，只能用 Responses。
16. **Claude/Codex 生态的默认口径**：官方 Codex 集成用 `base_url = "https://api.deepseek.com/"` + `wire_api = "responses"`，模型条目里 `context_window: 1048576`、支持的 reasoning effort 为 `low/high/max`（[codex-guide]）。这与 §1.3 的模型表一致。

---

## 6. 错误、限流、超时

### 6.1 错误状态码（[errors]）

| 状态码 | 含义 | 处理建议 |
| --- | --- | --- |
| 400 | 请求体格式非法 | 按错误信息改请求体；本文里 400 的高频原因：Chat 思考模式下用了 `tool_choice: required/具名`、schema 不合 strict 要求、Responses 超上下文 |
| 401 | 鉴权失败 | 检查 API key |
| 402 | 余额不足 | 充值 |
| 422 | 参数非法 | 按错误信息改参数 |
| 429 | 触发限流（并发/QPS） | 退避重试，见 §6.3 |
| 500 | 服务端错误 | 短暂等待后重试 |
| 503 | 服务端过载 | 短暂等待后重试 |

**错误响应 JSON 结构**：官方 Error Codes 页面**只列状态码，没有固定 JSON schema**。与 OpenAI 兼容的常见形态是
`{"error": {"message": "...", "type": "invalid_request_error", "param": null, "code": "..."}}` —
但本文未能在文档中找到逐字段保证，代码里请**防御式解析**（优先 `error.message`，其次原始 body 文本），不要强依赖 `type` / `code` 的具体取值。（本次也未能取得未鉴权时的响应体，网关返回 401 但 body 为空。）

### 6.2 Keep-alive 与超时（[ratelimit]）

- 请求发出后到真正开始推理之间，连接保持，可能持续收到：
  - 非流式：空行（empty lines）；
  - 流式：SSE 注释 `: keep-alive`。
  这些不改变 JSON body，自己解析 HTTP 时要把它们跳过/忽略。
- **如果 10 分钟内还没开始推理，服务端会断开连接**。除此之外官方没有给出"整条响应最长多久"的承诺。
- 工程建议（非官方）：连接超时设短（如 10s），读超时设长；流式解析做"空闲超时"（例如 120s 无任何事件则重连/重试），整条请求上限自行兜底（如 600s）。这些是推荐值，不是文档承诺。

### 6.3 限流与重试

- 限流是**账号级并发限制**，与用哪个 API key 无关：`deepseek-flash` 2500、`deepseek-v4-pro` 500；超限返回 **429**（[ratelimit]、[pricing]）。
- `user_id`（Chat）/ `user`（Responses）可用于同账号下细分用户的内容安全、KVCache 隔离和调度隔离；普通账号所有 `user_id` 合并计算并发（[ratelimit]）。
- 重试建议（非官方）：
  - 429 / 500 / 503：指数退避 + 抖动重试（如 1s、2s、4s、8s，最多 4–5 次）。
  - 400 / 401 / 402 / 422：不要重试，改请求/配置。
  - 流式：在**尚未输出任何内容**前失败可以安全重试；已输出部分内容后重试要注意去重（Chat 侧看 `finish_reason` 是否 `aborted` / `insufficient_system_resource`，Responses 侧看 `response.failed`）。
  - 工具调用轮次的重试要幂等：同一个 `call_id` / `tool_call_id` 的工具结果最好能被缓存复用。

---

## 7. 一句话结论（给写代码的人）

- base URL 用 `https://api.deepseek.com`，模型用 `deepseek-flash` / `deepseek-v4-pro`，不要加 `/v1`。
- Chat Completions：`POST /chat/completions`，SSE 只发 `data:`，文本/推理/工具参数分别在 `delta.content` / `delta.reasoning_content` / `delta.tool_calls[i].function.arguments`，**以 `data: [DONE]` 结束**，`usage` 在最后一个 chunk。
- Responses：`POST /responses`，`event:` + `data:`（含 `type`、`sequence_number`），**以 `response.completed` / `response.incomplete` / `response.failed` 结束，没有 `[DONE]`**，`usage` 在终态事件的 `response.usage` 里。
- 两边都无状态；Chat 历史回传 `messages`，Responses 历史回传 `input`（`function_call` + `function_call_output` 用 `call_id` 成对配对）。
