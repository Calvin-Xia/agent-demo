# multitemporal-rs-agent

面向多时相遥感影像分析的 Agent 实验仓库。

本项目围绕“大语言模型智能体的工具调用策略与任务分解能力”展开，目标是在多时相遥感影像分析场景中，构建一个简单、透明、可复现的 Agent 框架，并逐步比较不同工具组织与任务分解策略的实际表现。

## 研究目标
- 独立自主搭建一个创新性agent，旨在解决多时相遥感影像分析的痛点，具有建设性意义。
- 构建可观察、可测试的最小 Agent 执行循环；
- 接入遥感影像读取、指数计算、变化分析等基础工具；
- 比较显式工具集、工具预筛选与按需工具发现等调用策略；
- 研究复杂遥感任务的分解、执行、验证与纠错过程；
- 建立可复现的实验任务、评价指标与结果记录方式。

## 当前阶段

仓库目前提供两条可运行路径：

- **scripted 离线 Demo**（无需 API Key）：`inspect_image(path)` 单步 Agent，用结构化轨迹展示 `task → decision → action → observation → final answer`。它使用固定的 scripted policy，只用于验证 Agent 轨迹、工具接口和安装流程，不包含 LLM、GeoTIFF 专用信息或真正的多时相分析。
- **最小 LLM harness**：`chat` 子命令把真实模型接到 `inspect_image` / `inspect_raster` 工具上，支持 chat/completions 与 responses 两种线格式、`.env` 配置和命名档案切换，详见「最小 Agent harness」章节。

## 运行最小 Demo

安装锁定环境、检查仓库内示例图片并运行测试：

```bash
uv sync --locked
uv run python -m rs_agent inspect examples/sample.ppm
uv run pytest -q
```

命令输出 JSON 形式的完整五阶段轨迹：`task` 记录输入任务，`decision` 展示固定策略的工具选择，`action` 记录调用参数，`observation` 保存工具结果，`final` 给出受控的最终状态和答案。

代码、数据流、测试与失败收束的完整拆解见 [`docs/minimal-image-agent-tool-walkthrough.md`](docs/minimal-image-agent-tool-walkthrough.md)。

## 检查遥感栅格元数据

系统能够执行 `gdalinfo` 时，可以读取单个栅格的低成本元数据：

```bash
uv run python -m rs_agent inspect-raster path/to/image.tif
```

该命令只保留驱动、尺寸、波段、坐标系、地理变换、像元大小和四角坐标等紧凑字段。当前不计算统计信息，也不执行多时相分析。

字段映射、外部命令边界、测试与受控失败机制见 [`docs/raster-metadata-inspection-tool-walkthrough.md`](docs/raster-metadata-inspection-tool-walkthrough.md)。

## 最小 Agent harness：接入 DeepSeek / OpenAI 兼容模型

`chat` 子命令把一个真实模型接到现有的 `inspect_image` / `inspect_raster` 工具上，组成一条最小但可观察的 Agent 循环：

```text
用户提示词 → 系统提示词 + 工具 JSON Schema → 模型流式输出
          → 工具调用 → 工具结果回填 → 模型用自然语言回答
```

两种 OpenAI 兼容线格式都支持：

- `--api chat`（默认）：`POST {base_url}/chat/completions`；
- `--api responses`：`POST {base_url}/responses`。

### 用 `.env` 保存本地 key

凭据只从本地读取、不进仓库。复制示例文件再填 key 即可：

```bash
cp .env.example .env
# 编辑 .env：DEEPSEEK_API_KEY=sk-...
```

`.env` 已被 `.gitignore` 忽略；[`.env.example`](.env.example) 里列出全部可选项。支持这些键：

| 键 | 取值 | 说明 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` / `RS_AGENT_API_KEY` | `sk-...` | API key |
| `RS_AGENT_BASE_URL` / `DEEPSEEK_BASE_URL` | 如 `https://api.deepseek.com` | 自定义端点 |
| `RS_AGENT_MODEL` | 如 `deepseek-flash`、`deepseek-v4-pro` | 模型 id |
| `RS_AGENT_API` | `chat` / `responses` | 线格式 |
| `RS_AGENT_THINKING` | `enabled` / `disabled` | 思考模式开关 |
| `RS_AGENT_REASONING_EFFORT` | `low` / `high` / `max` | 思考强度（不写 `THINKING` 时会自动启用思考） |

配置优先级：**CLI 参数 > 环境变量（`RS_AGENT_*`，其次 `DEEPSEEK_*`）> `.env` > 内置默认值**。`--env-file` 可指定别的路径（默认当前目录的 `.env`）。

### 自定义 baseURL / key

三种方式任选，效果等价：

```bash
# 1) 写进 .env（推荐，之后自动读取）
RS_AGENT_BASE_URL=https://your-proxy.example/v1
RS_AGENT_API_KEY=sk-your-own-key
RS_AGENT_MODEL=your-model-id

# 2) 环境变量（仅当前终端有效）
$env:RS_AGENT_BASE_URL='https://your-proxy.example/v1'; $env:RS_AGENT_API_KEY='sk-your-own-key'

# 3) 单次运行用 CLI 参数（优先级最高）
uv run python -m rs_agent chat --base-url https://your-proxy.example/v1 --api-key sk-your-own-key --model your-model-id "检查 examples/sample.ppm"
```

`base_url` 只写到服务根路径，程序会自行追加 `/chat/completions` 或 `/responses`；如果第三方端点需要 `/v1` 前缀，就把 `/v1` 写进 base URL。启动横幅会显示实际生效的 endpoint、模型、协议、思考强度，以及 key 来源（命令行 / 档案 / 环境变量 / `.env`）和脱敏后的 key。

### 需要每次进入都选一遍吗？

不需要。把协议、模型、思考强度写进 `.env`（或存成配置档案）后，它们就是每次启动的默认值；交互模式加 `--no-menu` 可完全跳过选择菜单，启动即对话：

```bash
uv run python -m rs_agent chat --no-menu
```

不带 `--no-menu` 时菜单仍会出现，但每项默认值都来自 `.env`/环境变量/参数/档案，连续回车即可沿用；菜单里的自定义模型 id 只对当前会话生效，想固定下来就写进 `.env` 的 `RS_AGENT_MODEL`，或 `/save <name>` 存成配置档案（见下一节）。

### 多套配置切换：配置档案 + 斜杠命令

不想改 `.env` 也能保存多套配置并在运行时切换。档案存在本地 `.rs_agent_profiles.json`（已被 `.gitignore` 忽略），**不保存明文 key**，只记录 key 所在的环境变量名（`api_key_env`）。

在交互会话里用斜杠命令管理：

| 命令 | 作用 |
| --- | --- |
| `/help` | 显示全部命令 |
| `/profiles` | 列出已保存档案，`*` 标记当前 |
| `/config` | 显示当前生效配置（key 脱敏）与来源 |
| `/use <name>` | 切换到指定档案，**保留当前对话上下文** |
| `/save <name> [ENV]` | 把当前配置保存为档案；`ENV` 指定 key 的环境变量名（默认自动探测） |
| `/delete <name>` | 删除档案 |
| `/exit` | 退出 |

```text
>>> /save proxied
>>> /profiles
>>> /use deepseek-chat        # 切回原来的配置，旧档案原样保留
>>> /save proxy PROXY_API_KEY # 为另一套 key 建档案（key 本身放在 .env / 环境变量）
```

启动时的行为：

- `--profile <name>`：直接使用指定档案（一次性提问也生效）；
- 不带 `--profile`：自动沿用上次使用的档案（`last_profile` 持久化在档案文件里），无需每次重选；
- 交互模式会先出现"① 配置档案"选择（有多套档案时），再选协议/模型/思考强度；回车依次沿用；
- `--no-menu`：跳过菜单，直接用档案/`.env` 配置；`--no-profile`：忽略所有档案，只用 `.env` 与参数；
- `--profiles-file <path>`：指定档案文件位置（默认当前目录 `.rs_agent_profiles.json`）。

配置优先级：**CLI 参数 > 档案 > 环境变量/`.env` > 内置默认值**。切换档案会重建客户端但保留对话历史，因此可以在同一个会话里来回对比两套配置。

```bash
# 一次性提问（不弹菜单）
uv run python -m rs_agent chat "用中文说说 examples/sample.ppm 的尺寸和波段"

# 换成 Responses API 线格式
uv run python -m rs_agent chat --api responses "examples/sample.ppm 是什么格式？"

# 交互式多轮：有配置档案时先选档案，再选协议 / 模型 / 思考强度，/exit 退出
uv run python -m rs_agent chat

# 跳过选择菜单、直接用 .env/参数里的配置进入对话（适合脚本管道）
uv run python -m rs_agent chat --no-menu
```

交互式启动菜单把三项做成交互选择（存在配置档案时会多一步"① 配置档案"）：

```text
① 配置档案   1) 临时配置（.env/参数）  2) <已保存的档案> …（仅当有档案时出现）
② 接口协议   1) chat/completions    2) responses
③ 模型       1) deepseek-flash       2) deepseek-v4-pro   3) 自定义模型 id
④ 思考强度   1) 关闭  2) low  3) high  4) max
```

回车保留默认值（默认值来自 `.env`/环境变量/参数/档案）。启动横幅会显示最终生效的 endpoint、模型、协议、思考强度，以及 key 的来源（命令行 / 档案 / 环境变量 / `.env`）与脱敏后的 key。

默认关闭思考模式；可用 `--thinking enabled --reasoning-effort low|high|max`、`RS_AGENT_THINKING` / `RS_AGENT_REASONING_EFFORT`，或在菜单里选思考强度。其它常用参数：`--max-steps`、`--max-tokens`、`--temperature`、`--verbose`（stderr 打印 token 用量）。

输出约定：模型自然语言始终写入 stdout（流式增量打印），工具调用与观测轨迹写入 stderr，方便管道过滤与人工观察。

实现与接口依据：

- [`docs/minimal-harness-mvp.md`](docs/minimal-harness-mvp.md)：harness 组件、消息规范化与流式事件归一化设计；
- [`docs/deepseek-api-notes.md`](docs/deepseek-api-notes.md)：DeepSeek 双格式 API 的字段、SSE 事件与实现坑；
- [`docs/dsh-minimal-harness-notes.md`](docs/dsh-minimal-harness-notes.md)：DeepSeek Harness 中 dsh-context / 极简模式 / agent loop 的参考做法；
- [`docs/harness-verification.md`](docs/harness-verification.md)：最新一轮独立验收报告（v7，含逐项证据与遗留项）。

运行时依赖仍然只有 Pillow：HTTP 与 SSE 解析全部基于标准库，没有引入 OpenAI SDK 等额外依赖。

## 路线图

- [x] 建立 Python 与 `uv` 项目骨架；
- [x] 实现离线 `inspect_image(path)` 单步 Agent；
- [x] 添加确定性单元测试；
- [x] 添加持续集成；
- [x] 接入 OpenAI 兼容 LLM（chat/completions 与 responses 双格式）与最小 Agent harness；
- [x] 支持 `.env` 本地凭据、交互式配置菜单与命名配置档案（`/save` / `/use` 等斜杠命令）；
- [ ] 接入基础遥感影像处理工具；
- [ ] 设计多时相影像任务与评价指标；
- [ ] 开展工具调用策略和任务分解对照实验。

## 协作流程

`main` 是受保护分支，不直接向其推送代码。每项改动遵循以下流程：

1. 创建或认领 Issue；
2. 从 `main` 新建功能分支；
3. 完成修改并进行本地验证；
4. 提交 Pull Request，并关联对应 Issue；
5. 至少由一名队友 Review；
6. 处理反馈后合并到 `main`。

建议使用以下分支命名：

```text
feat/<short-name>
fix/<short-name>
docs/<short-name>
experiment/<short-name>
```

## 项目状态

Early development. 项目结构、接口和实验方案仍在持续讨论与迭代中。
