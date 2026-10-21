# DSH 参考实现调研笔记：context 插件、极简模式、agent loop

本文是对本机 DeepSeek Harness（DSH）安装的**只读**调研结果，目的是给仓库里的“最小 harness”实验提供可借鉴的实现参考，而不是复述 API 文档。DeepSeek HTTP 接口本身的事实、SSE 字段与两种方言的差异，请看同目录的 [DeepSeek API 调研笔记](deepseek-api-notes.md)。

调研过程中没有修改任何源码或配置，也没有读取凭据文件（`~/.dsh/.credentials.yaml` 未打开）；本文不含任何密钥/token。

## 0. 调研范围与方法

| 项 | 值 |
| --- | --- |
| 安装目录 | `C:\Users\Calvin-Xia\AppData\Local\Programs\DeepSeek Harness\resources` |
| 归档 | `app.asar`（约 182 MB），桌面壳 `@deepseek-ai/dsh-desktop` `0.2.1-rc.2` |
| 解包方式 | `npx --yes @electron/asar extract "<...>\app.asar" "$env:TEMP\dsh-asar"`（成功，约 1 分钟） |
| 运行时包 | `app.asar/dsh/node_modules/@deepseek-ai/*`（306 个 `@deepseek-ai` 包，其中 296 个为 `0.2.1-rc.2`，其余为 cordis/cosmokit/schemastery 等基础库，版本独立） |
| 另一份拷贝 | CLI 全局安装 `%APPDATA%\npm\node_modules\@deepseek-ai\dsh\node_modules\@deepseek-ai\*`（`0.2.0-rc.2`，与上面逐包同构，个别文件行号/体积略有差异） |
| 用户 profile | `~/.dsh/profiles/desktop`、`~/.dsh/profiles/web`（pnpm，插件经 junction 指向全局安装） |

下文路径中 `app.asar/dsh/...` 指 asar 归档内的路径；本机解包镜像在 `%TEMP%\dsh-asar\dsh\...`。文中引用的行号以 **0.2.1-rc.2（asar 运行时）** 为准。

侦察顺序：先看 `~/.dsh/profiles/desktop`（发现第三方插件 `dsh-context`），再解包 asar 定位第一方运行时，最后只精读 context / loop / prompt / tool / stream / compaction 相关文件。未通读全部 300 多个包。

## 1. 包地图（先看这张表）

| 关注点 | 包（均在 `app.asar/dsh/node_modules/@deepseek-ai/` 下） |
| --- | --- |
| 系统提示词装配 | `dsh-system-prompt`（`lib/index.js`）、`dsh-persona` |
| 上下文度量 | `dsh-token-meter`、`dsh-session-projection` |
| 上下文洞察（第三方） | `dsh-context`（`~/.dsh/profiles/desktop/node_modules/dsh-context`，v0.72.0） |
| agent 主循环 | `dsh-agent-loop`（`lib/index.js`，约 2000 行）、`dsh-agent`、`dsh-session` |
| LLM 抽象与流协议 | `dsh-llm`（`lib/types/assembler.js`、`lib/types/assistant-stream.js`） |
| 第一方 DeepSeek 适配器 | `dsh-llm-deepseek`（+ `dsh-llm-deepseek-api-key` / `-account`） |
| OpenAI 兼容方言 | `dsh-llm-pi-ai` + 依赖 `@earendil-works/pi-ai`（在 `~/.dsh/profiles/node_modules`） |
| 工具注册与执行 | `dsh-tools`（`lib/index.js`、`lib/types/schema.js`、`lib/types/json-schema.js`、`lib/types/ptc.js`） |
| 工具结果截断 | `dsh-output-retention`、`dsh-spill-policy`、`dsh-compaction-tool-result-pruner` |
| 压缩 | `dsh-compaction`、`dsh-compaction-basic`、`dsh-compaction-image-offload` |
| 预设 / 极简模式 | `dsh-agent-preset`、`dsh-agent-preset-registry`、`dsh-web-app/presets/{minimal,standard,ptc,cordis}.patch.yml` |
| 独立最小 bundle | `dsh-sdk-minimal`（自带完整 `cordis.patch.yml`，不叠加 `dsh-base`） |
| 重试 | `dsh-llm-retry`（在 `agent/request-error` 上执行供应商策略） |

整体架构一句话：**Cordis 插件树 + 事件溯源的 session 日志**。每个模型请求都是“当前日志的派生结果”，所有插件都通过 `session/event` 观察和折叠状态；UI、token 统计、压缩、上下文面板读的是同一份日志投影。

## 2. `dsh-context`：它是观测器，不是提示词拼装器

先纠正一个容易产生的误解：`dsh-context` **不是** DSH 的上下文/提示词实现。它是第三方插件（作者 bowenliang123，Apache-2.0，`dsh-context@0.72.0`），通过 profile bundle 挂载：

`~/.dsh/profiles/desktop/package.json` → `dsh.profile.bundles` 里包含 `"dsh-context"`。

它的实现方式（`lib/index.js` + `lib/client.js`，node 半 + web 客户端半）：

- **只读折叠**。节点侧注册 session 投影/事件监听，把 durable log 折成上下文面板需要的形状：每次请求的 `system`/`tool`/`user`/`assistant`/`toolResult`/注入块、压缩与裁剪事件、逐 step 的时间线。`lib/index.d.ts` 里能看到它的数据结构：`SystemPromptNode[]`、`ContextEventRecord[]`、`ContextTimeline`、`ContextActivity` 等。
- **复用官方度量**。占用率与构成不自己算，读的是 `contextPressure` / `contextBreakdown` 投影（与聊天输入框的 context ring 同源），只做更细的分类展示。官方 `contextBreakdown` 只有三类：`systemTokens` / `toolsTokens` / `messageTokens`（见 `dsh-token-meter/README.md`），`dsh-context` 在其上再拆出注入上下文、技能注入、工具结果等类目。
- **事件分类**：`Inject / Compact / Prune / Switch / Mode`，并标注产生者（instruction 文件、插件 id、技能名）与净 token 增量。
- **系统提示词注入点**：它的偏好设置里有 `promptPrefix` / `promptSuffix`（`lib/index.d.ts` 中 `/** Free text joined ahead of ... */ promptPrefix`），把用户自定义文本拼到每个 session 装配出的系统提示词**最前/最后**，所有 agent（含子 agent）生效，下一次 turn 生效。这属于“在装配结果外面再加一层”，不是提示词分层机制本身。

**结论**：系统提示词如何分层/拼接在 `dsh-system-prompt`（第 4 节），上下文管理与压缩在 `dsh-token-meter` + `dsh-compaction-*`（第 7.6 节）。`dsh-context` 值得借鉴的是“**同一份日志折叠出多个只读视图**”的插件形态，以及它的六类上下文构成条（Context Browser 里再拆出技能注入等 7 类折叠目录）；它不参与任何 loop 决策。

## 3. “极简模式”的两层含义

本机存在两个不同的“最小化”实现，职责不同：

### 3.1 Web/桌面里的 Minimal agent preset（用户可见的“极简模式”）

定义文件：`app.asar/dsh/node_modules/@deepseek-ai/dsh-web-app/presets/minimal.patch.yml`

这是一个 **agent preset 声明**（`@deepseek-ai/dsh-agent-preset`，`config.id: minimal`，`order: 3`），只插入 4 组子插件：

1. `persona`：`@deepseek-ai/dsh-persona`，`prefix: "You are a helpful software engineer assistant."`，`complete: true`（这一句就是完整系统提示词），`includeRuntimeContext: false`。
2. `tool-fs`：`@deepseek-ai/dsh-tool-fs`，但 `read/write/edit` 全关、只开 `readImage: true`（给多模态模型看图用）。
3. `compaction` 组（`isolate: { compaction: true }`）：`dsh-compaction-basic` + `dsh-command-compact`。
4. `persistent-shell` 组（`isolate: { terminals: true }`）：`dsh-terminal`（pty）+ `dsh-terminal-bash` + 持久 bash/pwsh 工具（Windows 下自动二选一），并自带一段工具描述文本。

Web 端 UI 对“极简模式”的官方说明（`dsh-client-ui-agent-preset/lib/client.js`，`guideMinimalExplanation`）与此一致：

> 一个持久 Shell 工具、`read_image`、固定系统提示词；上下文压力下自动压缩、空闲时可用 `/compact`；不加载 Skills、计划（planning）和标准运行时上下文。

拼装方式值得注意：**preset 不重新定义内核**。`dsh-web-app/cordis.patch.yml` 把原本挂在 host 平面的 agent 侧行（`tool-bash`/`tool-pwsh`/`tool-fs`/`tool-web`/`tool-todo`/`tool-jobs` …）统一 `disabled: true`，改由每个 session 在预设 realm 里挂载；内核（`llm`/`session`/`tools`/`agent`/`agent-loop`/`system-prompt`）仍来自共享的 `dsh-base` bundle。也就是说：**“极简”= 内核不变，能力子集 + 固定提示词**。扩展新预设只需再写一个 `presets/*.patch.yml`，这也解释了为什么 Creator 模式能“按行 id 覆盖”内置预设。

### 3.2 独立最小 harness bundle：`dsh-sdk-minimal`

如果问题是“从零搭一个最小 harness 需要哪些部件”，答案在 `app.asar/dsh/node_modules/@deepseek-ai/dsh-sdk-minimal/cordis.patch.yml`。它的注释直接写明：

> Unlike the ordinary SDK profile, this bundle does not layer over `dsh-base`: this insert is the complete Cordis tree.

即这份 YAML 就是**完整组件清单**（约 40 行插件），按职责归类：

| 层 | 行（插件） |
| --- | --- |
| 入口/传输 | `dsh-sdk-app`（profile 标记）+ `dsh-sdk-jsonrpc-server`（JSON-RPC 服务端） |
| LLM | `dsh-llm`（抽象）+ `dsh-llm-deepseek-api-key`（一个供应商适配器，`apiKeyEnv: DEEPSEEK_API_KEY`，`defaultContextWindow` 默认 1,000,000）+ `dsh-llm-retry` + `dsh-deepseek-llm-api-extensions` |
| Session | `dsh-session` + `dsh-session-persistence-jsonl`（`~/.dsh/sessions`，无压缩）+ `dsh-session-title` + `dsh-session-projection` |
| 提示词/工具 | `dsh-system-prompt`（`includeHarnessIdentity:false`、`includeRuntimeContext:false`、`personaPrefix` 取 `DSH_SYSTEM_PROMPT` 环境变量）+ `dsh-tools` + `dsh-tool-fs`（只开 `readImage`）+ 持久 shell |
| 循环 | `dsh-agent` + `dsh-agent-loop`（`agents: []`）+ 三个 `*/invariant` 伴生包 |
| 上下文管理 | `dsh-token-meter` + `dsh-compaction-basic` + `dsh-compaction-image-offload` |
| 环境 | `dsh-sandbox-local` + `dsh-sandbox-policy`（`danger-full-access`）+ `dsh-subprocess-local` + `dsh-fs-local` + `dsh-attachment-local` + `dsh-terminal`/`-bash` + `dsh-jobs-local` + `dsh-mcp-resources` + `dsh-working-directory` |

**这是全文最有价值的一张清单**：一个能跑的最小 harness ≈ 1 个入口 + 1 个 LLM 抽象 + 1 个供应商适配器 + session 日志（含持久化）+ 提示词装配 + 工具注册表 + agent loop + 压缩/度量。没有 UI、没有多 agent、没有规划、没有技能系统、没有 MCP 客户端也能成立。

### 3.3 各组件的实际职责（对应关系）

- **session 状态**：`dsh-session` 的 append-only 事件日志（`turn/start`、`step/start`、`system/message`、`user/message`、`assistant/message`、`tool/call`、`tool/result`、`step/end`、`turn/end`），外加可被替换的 “surface” 视图（压缩/裁剪会 shadow 旧节点）。落盘由 `dsh-session-persistence-jsonl` 挂在 loop 的写句柄上完成。
- **agent loop**：`dsh-agent-loop`，见第 5 节。
- **LLM provider**：`dsh-llm` 定义与供应商无关的 `Message` / `ContentBlock` / `StreamChunk`；适配器只负责“本地词汇 ↔ 供应商 wire”，并把流翻译成 chunk 序列。
- **tool registry**：`dsh-tools`，`defineTool` 声明式定义，注册即自动进入提示词装配的 tool schema 集合；执行走固定管线。
- **stream 适配**：适配器把 SSE 翻成 `StreamChunk`；`BlockAssembler` 再把 chunk 增量拼成块/消息。UI 侧不解析供应商事件。

## 4. 系统提示词如何分层 / 拼接 / 注入

实现文件：`app.asar/dsh/node_modules/@deepseek-ai/dsh-system-prompt/lib/index.js`。核心是一个注册表 `ctx.systemPrompt` 加一次协作式装配：`assemble()` 产出结构化 assembly，`renderPrompt()` 才把它变成字符串。

### 4.1 配置只拥有身份开关、运行时上下文开关、persona 前后缀与工具顺序

| 字段 | 默认 | 作用 |
| --- | --- | --- |
| `includeHarnessIdentity` | `true` | order `-1000` 的固定开场白：`You are an AI agent powered by DeepSeek Harness.` |
| `includeRuntimeContext` | `true` | 是否注入动态运行时上下文（变成 user-role 快照，不是 system） |
| `personaPrefix` | `''` | order `0`，位于一等公民指令之前 |
| `personaSuffix` | `''` | order `10200`，位于一等公民指令之后 |
| `toolOrder` | — | 显式的模型可见工具顺序，必须恰好一个 `'<unlisted-tools>'` 占位项 |

其余内容全部来自插件注册的 contribution；部署方没有“用户直接编辑 prompt”的入口（`dsh-system-prompt/README.md` 的 Known Limitations 明确写了这一点）。

### 4.2 分层 = 带 order 的 section

一等公民 section 的稀疏 order 分配（README 的 Model Experience 一节）：

| order | 内容 |
| --- | --- |
| `-1000` | harness 身份一句 |
| `0` | deployment persona prefix |
| `100`…`5000` | 可复用指令、工具使用指引（`tools:sdk` 在 5000，且关闭变量插值以保留字面 `{{…}}`） |
| `10000` | harness 来源/源码位置 |
| `10100` | Web surface 相关信息 |
| `10200` | deployment persona suffix |

插件可用任意有限 order 插入；相同 order 按 section 名字的 code-unit 顺序；scoped 注册（`agent.ctx.systemPrompt.section`）覆盖同名 global。**`complete: true` 的 section 在装配后成为完整系统提示词**，多于一个就报错——极简预设正是用它在 `@deepseek-ai/dsh-persona` 里把 prompt 钉成一句。

### 4.3 一次装配 = sections + contexts + tools + variables

```js
// dsh-system-prompt/lib/index.js（L117 附近，压缩示意）
function renderPrompt(assembly) {
  return assembly.sections
    .map((s) => (s.interpolate === false ? s.text : interpolate(s, assembly.variables, 'section')))
    .filter((text) => text.length > 0)   // 空 section 直接消失
    .join('\n\n');                       // 段落之间空一行
}
// assemble()：合并 global/scope 层 → 排序 section → 收集 tools/contexts/variables
// → 跑 system-prompt/assemble 瀑布 → 若有且仅有一个 complete section，强制它成为唯一 section
```

四个输入是并列的：

- `sections` → prompt 文本；`{{variable}}` 只在 render 阶段插值，未知变量/已注册但无值一律抛错（“畸形 prompt 比报错更糟”）。
- `contexts` → 带来源的动态运行时上下文，模型侧是 user-role 快照，可被 `suppressRuntimeContext()` 整体抑制。
- `tools` → 当前可见 `ToolSchema` 集合。**“模型被告知能做什么”和“模型能调什么”在同一次装配里产出**，虽然 wire 上 schema 是独立字段。
- `variables` → `{{model}}`、`{{cwd}}` 等，由 loop 提供，插件注册自己拥有的事实。

`toolOrder` 在瀑布前做规范化（注册顺序是插件加载产物，不可依赖）；列了未注册的工具名会让每次 `assemble()` 失败。`system-prompt/assemble` 瀑布的返回值是权威结果，之后才强制 complete section。

### 4.4 注入路径与 KV Cache 影响

- 模型只看到一条 **system-role 消息**：surface 节点 0，或在 in-history 模式下的“最新 system 节点”；loop 的 request 与 `request/header` 都不带独立 `system` 字段。
- 运行时上下文以 user-role 快照注入，文本带固定前缀：`Current runtime context. This snapshot supersedes earlier runtime-context snapshots.`
- 极简预设：persona `complete: true` + `includeRuntimeContext: false`，所以“固定系统提示词”名副其实。
- 第三方 `dsh-context` 的 prefix/suffix 属于装配**之后**的包裹层（见第 2 节），不是 section 机制。
- KV Cache：prompt 内容变了就地改写 system 节点，会让供应商前缀缓存从第一个变更 token 起失效；路由若声明 `systemPromptUpdate: 'in-history'`（默认目录里的 `deepseek-flash` 条目即声明此能力），非空新 prompt 会追加在历史之后，历史前缀缓存继续可用。工具 schema 变更无论哪种路由都会从变更处失效。

## 5. agent loop 实现细节

主文件：`app.asar/dsh/node_modules/@deepseek-ai/dsh-agent-loop/lib/index.js`（下称 `agent-loop`）。核心事实：**loop 不维护内存消息数组，每一步都从日志派生请求**。

### 5.1 单一事实源：日志 + surface

一次 step 的顺序（`agent-loop` 的 turn 循环与 `step()` 方法，约 L940–L1200）：

1. 开 turn：`session.append('turn/start')`。
2. `preStep()` 决定本轮输入（用户消息、steering、`agent/pre-step` 监听器的注入、压缩决策）；被拒绝或首轮无输入则不产生 step。
3. `step/start` → `agent/request` waterfall 决定 provider/model/effort → `llm.prepareCall()` 绑定额定适配器。
4. 系统提示词投影（`systemPrompt.project`）：按路由能力决定“就地改写 system 节点”还是“追加 in-history 更新”。
5. 首轮提交 `user/message`，写 `request/header`（config + tools），`deriveMessages()` 得到消息数组，深冻结请求。
6. 流式请求模型；每个 chunk 先完成持久化结算，再对外 emit；结束时写 `assistant/message`（含压缩后的 stream 记录）。
7. 有 tool-call 则执行，逐个 `tool/call` + `tool/result` 落日志，并把下一轮输入入队；无 tool-call 则结束。
8. `step/end`，必要时 `turn/end`（带 `completed | max-tokens | blocked | aborted | error`）。

因此“消息历史”= 日志中仍存活（未被压缩 shadow）的节点；“当前 prompt”= surface 上最后一个非空 `system/message`；“工具历史”= `tool/call` 与 `tool/result` 按 `callId` 配对，`tool/result` 事件带 `sourceEventSeqs` 指回 call 事件（见 `appendToolResult`，`agent-loop` L691 附近）。

### 5.2 工具调用的解析与回传

**解析**：模型只产出“工具 id + 名字 + 原始 JSON 字符串参数”。`BlockAssembler` 负责把 `tool-call-delta.argumentsDelta` 逐段拼回字符串；loop 里用 `parseArguments()` 宽容解析——非法 JSON 保留原文、空串当 `{}`、缺 id 用 `call-<index>` 兜底（`agent-loop` L535）。工具执行层再按 schema 强校验，参数不合法变成“普通错误结果”，不中断 turn。

**回传**（见 5.4 的两条链路）：无论哪种方言，回到内部都变成一条 `tool-result` 消息（`callId` + 内容块 + `isError`），再由适配器序列化为该方言的 wire 结构。**这是“双格式客户端”最值得照搬的结构**：只在边界做映射，内部只认一种工具结果表示。

### 5.3 流式文本如何增量输出（delta → UI 的桥接）

内部流协议是 7 种 chunk（`dsh-llm` 的 `StreamChunk` 联合类型，见 `assembler.js` 的 `push()`）：

```text
block-start(index, blockType) / block-end(index, block)
text-delta(index, text) / reasoning-delta(index, text)
tool-call-delta(index, id?, name?, argumentsDelta)
usage(usage) / finish(reason, replayState?)
```

桥接有三段：

1. **适配器 → chunk**：SSE 每帧翻成一个 chunk（见 5.4 的映射表）。
2. **chunk → 消息**：`BlockAssembler.push()` 增量累积；容忍“只有 delta 没有 block-start/end”的方言；`block-end` 先到者胜，之后的游离 delta 丢弃（防坏流撑爆内存）；流被取消时用 `interruptedBlocks()` 只保留已交付的文本/思考块，**不保留半截工具调用**（否则要伪造结果）。
3. **chunk → UI**：loop 规定“每次模型尝试只发一个进程内 `start`，所有 `chunk` 必须在对应的 durable assistant 帧结算**之后**才 emit，最后恰好一个 `end`”；`assistant/message` 事件里同时带完整消息和紧凑的 stream 记录，客户端/回放都读日志，而不是接收半成品。取消时落一个 `interrupted: true` 的 assistant anchor，保证“用户看到的”等于“下一轮请求包含的”。

### 5.4 两条链路各自的映射（重要：本机 DeepSeek 官方不走 chat/completions）

先说结论，避免按错误前提设计：

- **第一方 DeepSeek 适配器** `dsh-llm-deepseek` 走的是 **Anthropic Messages 方言**（默认 `https://api.deepseek.com/anthropic`，请求 `/v1/messages`，`x-api-key` 或账号 token），**没有** chat-completions / responses 分支。
- **chat completions 与 responses 两条链路**由 `dsh-llm-pi-ai` 适配器交给 `@earendil-works/pi-ai` 库处理；路由用 `api:` 字段声明方言：`openai-completions` / `openai-responses` / `anthropic-messages`。DSH 自己不手写这两套映射。

三方映射对照（同一“工具调用”场景）：

| 内部概念 | Messages（DeepSeek 官方，实际在用） | openai-completions（pi-ai） | openai-responses（pi-ai） |
| --- | --- | --- | --- |
| 助手工具调用 | `content: [{type:'tool_use', id, name, input}]` | `message.tool_calls: [{id, type:'function', function:{name, arguments: JSON字符串}}]` | input item `{type:'function_call', call_id, name, arguments}` |
| 工具结果 | `content: [{type:'tool_result', tool_use_id, content}]` | `{role:'tool', tool_call_id, content}` | item `{type:'function_call_output', call_id, output}` |
| 流式文本 | `content_block_delta` → text-delta | `choices[].delta.content` → text_delta | `response.output_text.delta` → text_delta |
| 流式工具参数 | `input_json_delta.partial_json` → tool-call-delta | `delta.tool_calls[].function.arguments` 增量拼 `partialArgs` | `response.function_call_arguments.delta` 增量拼 `partialJson` |
| 结束标志 | `message_stop` / `stop_reason` | `[DONE]` + `finish_reason` | `response.completed`（无 `[DONE]`） |

关键细节：

- 两条 OpenAI 链路在 pi-ai 内部都被归一化成 `AssistantMessageEvent`（`text_delta` / `thinking_delta` / `toolcall_delta` / `toolcall_end` / `done` / `error`），再由 `dsh-llm-pi-ai` 的 `toStreamChunks()` 翻成 DSH chunk（`app.asar/dsh/node_modules/@deepseek-ai/dsh-llm-pi-ai/lib/index.js` L1537 起）。其中 `toolcall_end` 会把**已解析的对象**重新 `JSON.stringify` 成字符串（L1615 附近），因为 DSH 内部约定工具参数始终是原始 JSON 字符串。
- Messages 方向的翻译在 `dsh-llm-deepseek/lib/index.js`：`tool-call ↔ tool_use`（L1565 附近）、`tool-result ↔ tool_result`（L1679 附近）、delta 翻译含 `input_json_delta`（L1902）、整体 `translate()`（L1928）。
- `stop_reason` 映射：`end_turn`/`stop_sequence → stop`，`tool_use → tool-calls`，`max_tokens → max-tokens`。
- 回放元数据（reasoning 签名、`responseId` 等）只在该适配器内部随消息走（`replayState`），跨适配器时丢弃；不同方言不共享缓存域。

### 5.5 工具结果如何序列化 / 截断

**序列化**：工具执行返回 `content`（内容块数组）+ `isError`；抛错或拒绝一律渲染成恰好 `Error: <message>` 一行，结构化错误信息（`error.info`）只进事件、不进模型可见文本（`dsh-tools/README.md` 的 Model Experience）。`finalizeContent` 是最后一道内容变换。

**截断**（三件套，按需组合）：

- `dsh-output-retention`：库级工具。`ItemRetainer`（有序列表，保留 head，可给**精确** omitted 数量）、`TextRetainer`（head/tail/head+tail 字节窗口，不切断 UTF-8）、`formatRetentionNotice()` 统一“省略了什么”的脚注。
- `dsh-spill-policy`：文本和图片共享一个估算 token 预算；超预算时模型收到 head/tail + **完整结果的落盘路径**，图片留在 attachment 存储里。
- `dsh-compaction-tool-result-pruner`：只在压缩触发之后生效，把超预算工具结果换成“有界头部 + 中间裁剪标记 + 有界尾部”，**原始结果仍留在日志里**可精确回放。

计量的统一口径是 `dsh-token-meter`：固定启发式“4 字符 ≈ 1 token + 块/角色开销”，确定性、不调模型；压缩阈值、占用率 UI、遥测共用一个 `ctx.tokenMeter.measure()` 结果。

### 5.6 回合终止条件

规则很短，集中在 `step()` 的返回值与 turn 循环：

```js
// agent-loop L1160 附近（压缩示意）
if (finish.kind === "max-tokens") return { kind: "max-tokens" };
const toolCalls = message.content.filter((b) => b.type === "tool-call");
if (toolCalls.length === 0) return { kind: "completed" };
const { concluded } = await executeToolCalls(/* … */);
return concluded ? { kind: "completed" } : null;
```

```js
// agent-loop L1000 附近（压缩示意）
if (turnEnds && this.inbox.nextStep.length === 0) {
  await this.dispatch.serial("agent/turn-stopping", { turn, signal });
}
if (turnEnds && this.inbox.nextStep.length === 0) break;
```

含义：

- **继续**：模型回复里有工具调用 → 执行后把“下一轮输入”入队，进入下一个 step；会话中有 steering/新消息 → turn 不结束（`inbox.nextStep` 非空）。
- **结束**：`finish` 为 `stop`/`tool-calls` 但已无工具调用、且队列为空 → `completed`；`max-tokens` → 结束该 turn；错误/取消 → `error`/`aborted`；`agent/pre-step` 拒绝 → `blocked`。
- **没有内置 turn 预算**（README 明确写了 “No built-in turn budget”）：防跑飞要靠插件在 `agent/turn-stopping` 等扩展点取消。单个工具结果可声明 `concludesTurn: true` 主动收敛。
- 失败收尾：已发出但未执行的工具调用补 `ABORTED_BEFORE_DISPATCH` 结果，未应答调用补 `TOOL_OUTCOME_UNKNOWN` / `TOOL_NOT_STARTED`，保证下一轮工具历史始终成对。

## 6. 工具定义如何生成 JSON Schema

不是装饰器，也不是手写 schema，而是 **TypeScript 类型推断 + 自定义 schema DSL**：

- `defineTool({ name, description, parameters, output: { schema, render }, execute })`（`dsh-tools/lib/types/schema.js` L274）。
- `parameters` 是“隐式对象”的逐属性表：`{ path: { type:'string', required:true, description }, offset:{type:'number'} }`；编译时收集 `required`，产出标准 `{type:'object', properties, required}`（`parameterSchemaSpecToJsonSchema`，L238）。
- 支持 `string/number/integer/boolean/null/array/object/json/oneOf`；编译后过一遍 `assertSupportedJsonSchema()`，只放行受控子集（不支持的组合在定义期就报错，而不是发给模型后被拒）。
- `output.schema` + `output.render(args, value)` 是**输出侧**：`execute` 只返回声明的 JSON 值，`render` 决定怎么变成模型可见内容；`output`/`execute`/`finalizeContent`/`timeoutMs` 都不会出现在 wire 上。
- 注册进 `ctx.tools` 后，schema 自动进入 `dsh-system-prompt` 的 tool provider（每步装配时取当前可见集）；`toolOrder` 负责确定性排序，`restrict()` 做 per-agent 白/黑名单，`deferLoading` 是“先声明后加载”。
- `mode: native | ptc | both` 决定呈现方式：`native` 直接发每个工具的 schema；`ptc` 只发一个 `run_code` + 生成的 SDK；`both` 都发（极简/标准预设都还是 native）。

## 7. 值得借鉴的 6 条做法

### 7.1 日志即状态：每步从日志派生请求，断言“模型可见 ⟺ 已记录”

`app.asar/dsh/node_modules/@deepseek-ai/dsh-agent-loop/lib/index.js`（`step()` 与 turn 循环，L940–L1200；下为压缩示意，`step/end` 实际在 `finally` 里）

```js
while (true) {
  const decision = await this.preStep(target, { turn, step });   // 本轮输入 + 预压缩
  if (turnEnds && decision.messages.length === 0) break;
  this.session.append("step/start", { turn, step });
  const stepEnd = await this.step(decision);                     // 请求/流/工具都在里面
  if (turnEnds === null || turnEnds.kind !== "max-tokens") turnEnds = stepEnd;
  this.session.append("step/end", { turn, step });
  if (turnEnds && this.inbox.nextStep.length === 0) break;
  target = "next-step";
}
```

**借鉴点**：不要维护“消息数组 + 历史摘要”两套真相；把请求写成日志的纯函数。重试、恢复、UI 回放、token 计量全部自动一致。

### 7.2 统一 chunk 协议 + 容错的增量装配器

`app.asar/dsh/node_modules/@deepseek-ai/dsh-llm/lib/types/assembler.js`

```js
case 'text-delta':
case 'reasoning-delta': {
  const p = this.ensure(chunk.index, chunk.type === 'text-delta' ? 'text' : 'reasoning');
  if (p.block) return;                    // block-end 之后的游离 delta 丢弃
  p.text += chunk.text; return;
}
case 'tool-call-delta': {
  const p = this.ensure(chunk.index, 'tool-call');
  if (p.block) return;
  p.toolCallId = chunk.id;
  if (chunk.name) p.toolCallName = chunk.name;
  p.toolCallArguments += chunk.argumentsDelta; return;   // 参数始终按字符串拼接
}
```

**借鉴点**：方言适配只发生在更外层；装配器同时容忍“有 delta 无 block 边界”的协议，`finish.kind === 'max-tokens'` 时丢弃不可安全执行的 tool-call。

### 7.3 工具定义 = 隐式参数表 → JSON Schema，执行前强校验

`app.asar/dsh/node_modules/@deepseek-ai/dsh-tools/lib/types/schema.js`（L238 / L274）

```js
export function defineTool(options) {
  const parameters = parameterSchemaSpecToJsonSchema(options.parameters); // 收集 required
  const validate = (args) => validateJsonSchemaValue(parameters, args, '');
  return {
    name: options.name,
    description: options.description,
    parameters,
    output: { schema: valueSchemaSpecToJsonSchema(options.output.schema),
              render: (a, v) => options.output.render(a, v) },
    async execute(args, exec) {
      const violations = validate(args);
      if (violations.length > 0) throw new ToolArgsError(violations);
      return options.execute(args, exec);   // 只返回声明的 JSON 值
    },
  };
}
```

**借鉴点**：声明和实现同源，schema 自动还有类型；参数校验失败是普通工具错误（`INVALID_ARGS`），不会升级成 turn 失败。

### 7.4 并发调度：独占调用做 barrier，安全调用走有界池，结果按模型顺序提交

`app.asar/dsh/node_modules/@deepseek-ai/dsh-agent-loop/lib/index.js`（`executeToolCalls` L506 / `runGroup` L551）

```js
async function executeToolCalls(ctx, turn, step, toolCalls, signal, acceptContext) {
  const planned = toolCalls.map((block) => ({ block,
    exec: { callId: block.id, name: block.name, arguments: parseArguments(block.arguments), signal } }));
  let next = 0;
  while (next < planned.length) {
    const mode = ctx.tools.executionMode(planned[next].exec).kind;   // parallel | exclusive
    const group = mode === 'parallel' ? planned.slice(next) : [planned[next]];
    const outcome = await runGroup(ctx, turn, step, group, mode, signal, acceptContext);
    next += outcome.consumed;    // 组内用 rolling pool 消费，maxParallelToolCalls 默认 10
  }
}
```

**借鉴点**：用“每个工具自己声明是否并发安全”代替全局开关；结果只在轮到它时才提交，所以日志顺序永远等于模型顺序，取消时已启动的会 drain 完再收尾。

### 7.5 系统提示词 = 有序 section + 变量插值 + 工具 schema，一次装配（详见第 4 节）

`app.asar/dsh/node_modules/@deepseek-ai/dsh-system-prompt/lib/index.js`（`assemble` L356，`renderPrompt` L117）

```js
const sectionByName = this.layers.merge(scope, (l) => l.sections);      // scoped 覆盖 global
const sections = [...sectionByName.values()].sort(comparePromptSections); // 先 order 再名称
const complete = sections.filter((s) => s.complete === true);
if (complete.length > 1) throw new Error('multiple complete prompt sections are active');
const assembly = {
  sections: sections.map((s) => ({ name: s.name, text: resolve(s.text, ctx) })),
  contexts: runtimeSuppressed ? [] : sortedContexts,                    // 动态运行时上下文
  tools: orderTools(collected, this.toolOrder, knownNames),             // schema 也算装配结果
  variables,
};
return await this.ctx.waterfall(target, 'system-prompt/assemble', assembly, ctx, () => assembly);
// 渲染：section 按插值规则替换 {{var}}，丢空段，用空行连接
```

**借鉴点**：提示词不是拼字符串，而是一个可被插件瀑布修改的结构化对象；“工具说明”和“工具 schema”在同一处装配，保证模型看到的“能做什么”和“能调什么”永远同步；`complete: true` 让极简模式一句话覆盖整个 prompt，同时仍能拿到 tool schema。

### 7.6 压缩：先无模型裁剪，再摘要；摘要复用 warm prefix，替换帧显式标记

`app.asar/dsh/node_modules/@deepseek-ai/dsh-compaction-basic/README.md`

```text
触发阈值 = floor(min(W × thresholdRatio, W − O − headroomTokens))
默认：thresholdRatio = 0.8，headroomTokens = 65536，retainRatio = 0.16
范围：从第一个非 system 节点开始的“最老平衡段”，结尾保留 16% × (W − O) 原文
替换：一条 user 消息，内含 <compacted-summary>…</compacted-summary>
```

**借鉴点**：裁剪（无模型调用）先于摘要，够用就不必付额外请求；摘要请求按“system 节点 0 + 被压缩段 + 尾部 instruction”原样重放，命中供应商前缀缓存；压缩是“替换区间”而不是追加，日志里先写 `compaction/start`，失败留下可诊断的孤儿标记。

## 8. 对本仓库实验的启示与注意点

1. **不要按“DSH 有 chat/responses 双链路”去理解它**：第一方 DeepSeek 路由是 Messages；双方言在第三方 pi-ai 适配器里。若目标是写一个 Python 最小 harness 同时支持两种方言，更贴近 DSH 的做法是：**定义内部归一化事件（text/reasoning/tool-call/done）+ 两个薄适配器**，而不是让 loop 感知方言。
2. **工具参数在内部一律用“原始 JSON 字符串”**：流式拼接简单，增量 delta 可以直接透传；到执行层再宽容解析 + schema 校验。pi-ai 在 `toolcall_end` 把对象重新序列化回字符串，就是为了守住这条内部约定。
3. **流式输出与持久化顺序不能反**：先落 assistant 帧再向前端 emit，否则取消/断线后“用户看到的”与“下一轮请求里的”不一致。
4. **两种方言的工具结果都必须能配对回 call id**：Messages 用 `tool_use_id`，completions 用 `tool_call_id`，responses 用 `call_id`；内部统一成 `callId`，日志里用 `sourceEventSeqs` 指回调用事件。
5. **极简模式不是“删代码”，而是“换配置”**：内核复用、能力用 preset 行声明开关（可 `disabled: !!js …` 按平台），提示词一句话 `complete` 覆盖。这比维护一个分叉的最小实现省得多。
6. **密钥只以“引用”形式进配置**：`apiKeyEnv: DEEPSEEK_API_KEY`，运行时经 credential seam 解析；配置文件里不出现 key，失败用 `MISSING_CREDENTIAL` / `INVALID_CREDENTIAL` 明确区分。

## 9. 复现命令（全部只读）

```powershell
# 1) 解包 asar（约 1 分钟，输出到工作区外）
npx --yes @electron/asar extract "C:\Users\Calvin-Xia\AppData\Local\Programs\DeepSeek Harness\resources\app.asar" "$env:TEMP\dsh-asar"

# 2) 只看最小 harness 的组件清单
Get-Content "$env:TEMP\dsh-asar\dsh\node_modules\@deepseek-ai\dsh-sdk-minimal\cordis.patch.yml"

# 3) 极简模式预设
Get-Content "$env:TEMP\dsh-asar\dsh\node_modules\@deepseek-ai\dsh-web-app\presets\minimal.patch.yml"

# 4) 本机已安装的第三方 context 插件
Get-ChildItem "$env:USERPROFILE\.dsh\profiles\desktop\node_modules\dsh-context" -Recurse -File

# 5) 跑起来的最小验证（不需要 UI）
#    dsh --profile sdk-minimal ... 或 dsh plugin list --profile desktop
```
