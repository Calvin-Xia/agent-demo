# 最小 Harness MVP 独立验收报告

- 验收人：verifier（独立于实现者）
- 验收日期：2026-10-21（Asia/Shanghai）；历史轮次 v2–v5 于 2026-10-20
- 验收对象：`python -m rs_agent chat` 的最小 Agent harness（`src/rs_agent/llm.py`、`harness.py`、`__main__.py`）、本轮新增的配置档案模块 `src/rs_agent/profiles.py` 与斜杠命令 `--profile` / `--no-profile` / `--profiles-file` / `/profiles` `/config` `/use` `/save` `/delete` / 启动菜单「① 配置档案」，及其测试
- 验收方式：只读代码审查 + 黑盒真实 E2E + 独立新增冒烟测试 + 本地故障注入（不修改实现文件）
- 真实 API：DeepSeek `https://api.deepseek.com`，模型 `deepseek-flash`，`chat/completions` 与 `responses` 两种线格式
- 验收基线：**v9**（PR #8 Codex review P1/P2 修复）。历史：v2 59、v3 61、v4 73、v5 74、v6 97、v7 100、v8 101 passed；v9 为 **104 passed**。本报告证据均取自 v9（commit `c585cb6`）；v9 仅改交互菜单/`/use` 的配置隔离逻辑，harness/协议/工具行为不变，相关证据沿用 v7/v8

## 1. 结论

**通过，无阻塞项。**

v9 复验结果：**104 个测试全绿（31 个旧测试 + 73 个新测试）**。v9 修复 PR #8 的 2 条 Codex review finding：**P1**（交互菜单答案被写进 args，随后 `/use` 把它们当成 CLI 覆盖，导致切档后协议/模型/思考强度仍是旧档案）与 **P2**（已激活档案时选「临时配置」后，默认值仍来自旧档案）。修复后菜单选择与真实 CLI flag 正确隔离：`/use` 用新档案配置，真实 CLI flag 仍优先；菜单选临时配置时默认值来自 `.env`/环境变量。既有能力（双向言工具调用、多轮、思考模式回传、`.env`、配置档案与斜杠命令、错误路径、横幅脱敏、路径保持）继续通过：

- 配置档案读写：`/save` 生成的 JSON **只含 `api_key_env`，无明文 key**；`last_profile` 持久化；原子写无残留临时文件；`/delete` 生效；
- 档案消费：`--profile` 一次性生效并更新 last；二次启动 `--no-menu` 自动沿用 last；`--no-profile` 一次性忽略档案；未知档案与损坏 JSON 均退出码 2、错误可读、无栈；
- 运行时切换：`/use` 重建 client、切换协议/模型，**对话历史保留**；`/profiles` `/config` 均脱敏；
- 菜单：有档案时出现「① 配置档案」，选定后走完协议/模型/思考强度，横幅为所选档案；EOF 取默认；
- 真实 E2E：两套档案（chat / responses）各跑一次工具调用，均 exit 0。

历次问题状态：v2 修复「流截断/`response.failed` 静默成功」「`--max-steps` 打栈」「交互提示符污染 stdout」；v3 修复 R1（读流异常未包装）与 R2（截断无提示）；v4 修复空环境变量覆盖 `.env`；v5 修复 R4（`.env` BOM/行内注释）；v6 新增配置档案/斜杠命令并发现 **R5（Low）**：`--no-profile` 在交互菜单路径被忽略并会清空 `last_profile`；**v7 已修复 R5**（`--no-profile` 全路径生效、与 `--profile` 互斥）。当前仅保留 1 个信息项 R3（不做重试/超时不可配，属文档声明的刻意范围）。

v8 维护项（低风险，已完成）：`_band_mean()` 的 `Image.getdata()` → `Image.get_flattened_data()`（Pillow ≥12.1，语义一致），消除 16-bit 均值路径的 Pillow DeprecationWarning；新增回归测试 `test_inspect_image_preserves_16bit_mean_without_deprecation_warning`。统计值、harness 行为与 CLI 输出均不变（证据见 §3.4）。

v9 review 修复（PR #8 Codex P1/P2，commit `c585cb6`，已完成）：新增 `_args_without_menu_overrides()`，`_rebuild_session()` 在 `/use` 时只保留真实 CLI flag、丢弃菜单答案（P1）；`_interactive_menu()` 对选择的档案（**包括「临时配置」None**）都 `build_config(profile)` 重建默认值（P2）。新增 3 个回归测试；独立 E2E 证据见 §3.5。

## 2. 验收基线（v9，commit c585cb6）

v7 基线提交 `eefabd0`；v8 维护改动提交 `0a99919`；v9 是 **PR #8 Codex review 修复，提交 `c585cb6`（已推送）**，采集基线时工作区干净。以下 SHA-256 前 16 位为本次 v9 验收基线；v9 只改 `__main__.py` 与两个菜单/斜杠测试文件，其余文件自 v7/v8 未变，harness/协议/工具行为不变：

| 文件 | SHA-256（前 16） | 字节数 |
| --- | --- | --- |
| `src/rs_agent/profiles.py` | `89A4D4E866360FB5` | 8055 |
| `src/rs_agent/__main__.py` | `02C430FBFDE632CA` | 25787 |
| `src/rs_agent/llm.py` | `3D3FC8CC778D8F0E` | 23134 |
| `src/rs_agent/harness.py` | `846E668302254678` | 9983 |
| `src/rs_agent/tools.py`（v8 维护） | `2B2D9B94AFEE42D8` | 11111 |
| `tests/test_profiles.py` | `D23E4EC48D9EF52A` | 4590 |
| `tests/test_slash_commands.py` | `F9C9EEB8E0B97073` | 11119 |
| `tests/test_interactive_menu.py` | `24FD94C3C786507B` | 5801 |
| `tests/test_cli_smoke.py` | `893F1BC5A27880C6` | 5902 |
| `tests/test_llm.py` | `98583274F5F8C3F3` | 17682 |
| `tests/test_harness.py` | `07C1AE845BAF6454` | 7001 |
| `tests/test_tools.py`（v8 维护） | `317B02A127D6A6AC` | 3162 |
| `.env.example` | `2F3FE95372C8D433` | 1353 |
| `.gitignore` | `63B4D22CB99CE18D` | 550 |

本地 `.env`（真实 key）与 `.rs_agent_profiles.json`（两套示例档案）**不纳入版本控制**：`git check-ignore` 命中 `.gitignore:35:.rs_agent_profiles.json*` 与 `.env`，`git status` 中均不出现；本报告不记录其内容。v9 复验期间 `.env` SHA-256 未变（`FA9530F4…`）；`.rs_agent_profiles.json` 在本轮复验开始前已被 Lead 的手工 API 复验更新（mtime 15:01:15，`last_profile=None`），verifier 未修改它；其 profile 只含 `api_key_env`，无明文 key。依赖未变：仍只有运行时 Pillow、测试 pytest；`uv.lock` 未改动，无新增第三方依赖。

## 3. 环境与测试结果

### 3.1 依赖同步

```powershell
uv sync --locked
# Resolved 8 packages in 0.92ms
# Checked 8 packages in 0.66ms
```

### 3.2 测试套件

```powershell
uv run pytest -q
# 104 passed, 1 warning in 1.12s
```

| 分组 | 命令 | 结果 |
| --- | --- | --- |
| 全套 | `uv run pytest -q` | **104 passed**（1 warning，仅 `.pytest_cache` ACL 的 cacheprovider 警告） |
| 原有旧测试（4 个文件） | `uv run pytest tests/test_agent.py tests/test_main.py tests/test_raster_tools.py tests/test_tools.py -q` | **31 passed**（`test_tools.py` 7，v8 新增回归测试） |
| 新增测试 | 6 个新测试文件 | **73 passed**（22 + 9 + 7 + 10 + 9 + 16） |

| 测试文件 | 用例数 | v6 变更/覆盖 |
| --- | --- | --- |
| `tests/test_llm.py` | 22 | 协议/SSE/配置/读流异常/`.env`；v6 新增 thinking 环境变量与 `.env` 解析测试（`test_resolve_config_thinking_from_env_and_dotenv` 等） |
| `tests/test_harness.py` | 9 | 工具循环、截断提示等（v3 起稳定） |
| `tests/test_cli_smoke.py` | 7 | CLI 参数、无 key、非法 `--api`；v6 起“缺 key”用例加 `--no-profile` 隔离本地档案 |
| `tests/test_interactive_menu.py` | 10 | 菜单默认/选择/重试/EOF/`--no-menu`；v6 扩展档案入口；v9 新增 `test_menu_temporary_choice_rebuilds_defaults`（P2） |
| `tests/test_profiles.py` | 9（v6 新增） | 缺失文件为空、增删改与 last 持久化、损坏 JSON/未知字段拒绝、字段校验、roundtrip 与脱敏、`from_config` 不落明文 key、原子写同目录临时文件且清理、写失败转 `ProfileError` |
| `tests/test_slash_commands.py` | 16（v6 新增） | 参数暴露、`/save` 无明文 key/显式 ENV、`/profiles` 标记、`/use` 切档保历史、`/use` 不存在、`/config` 脱敏、`/delete`、`/help`/未知命令/`/exit`、last 档案自动复用、未知档案退出码 2；v7 新增 3 个测试（两 flag 互斥、`--no-profile` 菜单忽略档案、显式选号不应用档案）；v9 新增 2 个测试：`test_use_after_menu_selection_applies_new_profile`（P1）、`test_use_keeps_true_cli_flags_authoritative` |

v9 后全量仅剩 1 个 warning：`.pytest_cache` 写入警告（本机该目录 ACL 受限，与代码无关）；Pillow `getdata` 弃用警告已随 v8 维护改动消失。`pytest tests/test_tools.py -W error::DeprecationWarning` = 7 passed，全量 `pytest -q -W error::DeprecationWarning` = **104 passed**（v9 复验时同样通过）。

### 3.3 旧 CLI 回归

```powershell
uv run python -m rs_agent inspect examples/sample.ppm          # EXIT=0
uv run python -m rs_agent inspect examples/missing.png          # EXIT=1，status=failed
uv run python -m rs_agent inspect-raster examples/missing.tif   # EXIT=1，JSON 错误可读
git diff --check                                                # EXIT=0
```

### 3.4 v8 维护改动：`getdata()` → `get_flattened_data()`

- 改动范围：仅 `src/rs_agent/tools.py::_band_mean()` 的高位深分支（`I` / `I;16` / `I;16B` / `I;16L` / `F`）把 `sum(band_image.getdata())` 换成 `sum(band_image.get_flattened_data())`；8-bit 分支仍走 `ImageStat.Stat().mean`，未改动；
- 依据：`get_flattened_data()` 由 Pillow 12.1.0 引入，与 `getdata()` 语义一致；项目依赖下限本就是 `pillow>=12.1.0`（当前锁文件解析到 Pillow **12.3.0**），无需新增依赖；
- 独立等价性验证（verifier 自写脚本，非复用单测）：
  - `I;16` `[0, 65535]` → 新旧算法均 `32767.5`；`I` `[0, 100000]` → 均 `50000.0`；`F` `[0.0, 1.5]` → 均 `0.75`；
  - 保存为 16-bit PNG 后走 `inspect_image()` 全链路 → `{"I": {"min": 0, "max": 65535, "mean": 32767.5}}`；
  - `examples/sample.ppm` 的 `inspect` 输出与 v7 完全一致（2×2、RGB、三波段 min 0 / max 255 / mean 127.5）；
- 弃用警告：`uv run pytest tests/test_tools.py -W error::DeprecationWarning` = **7 passed**；全量 `uv run pytest -q -W error::DeprecationWarning` = **104 passed**（v9 复验；无 Pillow DeprecationWarning；唯一 warning 是 cacheprovider）；
- 新增回归测试：`test_inspect_image_preserves_16bit_mean_without_deprecation_warning`（断言 `inspect_image` 不产生 `DeprecationWarning` 且均值仍为 32767.5）；
- 结论：**低风险维护项，统计值、JSON 输出与 harness 行为均不变**；v8 未触碰 harness/协议/CLI 代码，§4/§5 的真实 E2E 与错误路径结论继续适用。

### 3.5 v9 PR #8 Codex review 修复（P1/P2）

- **P1**（原 `__main__.py` L646）：菜单答案写入 `args` 后，`/use <name>` 的 `_build_config` 把它们当成 CLI 覆盖，导致切档后协议/模型/思考强度仍是旧档案（混合配置）。修复：新增 `_args_without_menu_overrides()`，`_rebuild_session()` 用还原后的 args（只保留真实 CLI flag）解析新档案；进入菜单前的 CLI 值存于 `ChatState.extra["cli_overrides"]`；
- **P2**（原 L392）：已激活档案时在菜单选「临时配置」后 `profile=None`，但 config 仍来自旧档案，回车会把旧档案默认值抄进 args。修复：`_interactive_menu()` 只要选了档案（**包括 None**）就 `build_config(profile)` 重建默认值；
- 新增 3 个回归测试：`test_use_after_menu_selection_applies_new_profile`、`test_use_keeps_true_cli_flags_authoritative`、`test_menu_temporary_choice_rebuilds_defaults`；
- **独立真实 CLI 复验**（仓库内临时 profiles：`chatp`=chat/flash/disabled，`respp`=responses/v4-pro/enabled-high；验证后已删除）：
  - **P1**：菜单选 chatp → 协议/模型/思考均回车 → `/use respp`：横幅变为 `api=responses model=deepseek-v4-pro thinking=enabled(high)`（新档案的值，而非旧档案 chat/flash/disabled），随后工具调用成功，**EXIT=0**（约 3.2s）；
  - **P1（真实 CLI flag 仍优先）**：`--api chat` + 菜单选 respp → `/use respp`：横幅 `api=chat model=deepseek-v4-pro thinking=enabled(high)`，即协议保留 CLI flag、其余来自新档案；
  - **P2**：last=respp（responses/v4-pro/high），菜单选「临时配置」后协议/模型/思考默认索引均为 `[1]`（chat/flash/disabled，来自 `.env`/默认，而**不是**旧档案），横幅 `profile=(none) api=chat model=deepseek-flash thinking=disabled`，工具调用成功，**EXIT=0**（约 2s）；
  - 结论：P1/P2 均已修复，菜单/切档不再产生混合配置；v9 未改动 harness/协议/工具层。

## 4. 真实 E2E 证据

说明：验收命令不设置任何 key 环境变量，由 CLI 从仓库 `.env` 或档案的 `api_key_env` 读取；本报告与终端输出中 key 一律为 `sk-****`。所有捕获输出与测试档案文件都做了“是否包含 `.env` 真实 key”的扫描，结果为空（见 §9）。为遵守本机“Python 子进程不可写 `%TEMP%` 根目录”的限制，档案 E2E 使用仓库内临时目录（§4.1/§4.4/§4.5 的取证为 v6 基线 `v6verify/`；v7 的 R5 复验使用 `v7verify/`；v9 的 P1/P2 复验使用 `v9verify/`），验收后均已删除，仓库无残留。

### 4.1 档案读写（`/save`、`/profiles`、`/config`、`/delete`）

新档案文件 `v6verify/profiles.json`（不存在时）上执行：

```text
>>> /save localchat
已保存档案 localchat（key 引用 DEEPSEEK_API_KEY）。之后可用 /use localchat 或 --profile localchat 使用。
>>> /config
profile=localchat endpoint=https://api.deepseek.com model=deepseek-flash api=chat thinking=disabled
key=sk-****（来源: 档案 localchat → DEEPSEEK_API_KEY） profiles_file=v6verify\profiles.json
>>> /profiles
已保存的配置档案：
 * localchat  (deepseek-flash / chat / https://api.deepseek.com)
```

落盘 JSON 结构（脱敏检查）：

```text
version= 1  last_profile= localchat  names= ['localchat']
localchat: keys=['api','api_key_env','base_url','model','thinking']
           api_key_env=DEEPSEEK_API_KEY  model=deepseek-flash  api=chat
           has_literal_key= False
```

断言：**无明文 key**、`api_key_env` 存在、`last_profile` 持久化、目录内无 `*.tmp` 残留（原子写 `os.replace` 成功）。`/delete localresp` 后 JSON 仅剩 `localchat`，若删除的是 last 档案则 `last_profile` 归 `None`；`/help` 正常列出全部命令。

### 4.2 启动参数与 last 记忆

| 场景 | 命令/构造 | 结果 |
| --- | --- | --- |
| `--profile` 一次性生效 | `chat --profile localchat "只回答 OK"` | **EXIT=0**；横幅 `profile=localchat`，key 来源 `档案 localchat → DEEPSEEK_API_KEY`；并把 last 写回 localchat |
| 重启沿用 last（`--no-menu`） | 空 stdin 二次启动 `--no-menu` | **EXIT=0**；横幅 `profile=localchat`（自动沿用 last） |
| `--no-profile` 一次性 | `chat --no-profile "只回答 OK"` | **EXIT=0**；横幅 `profile=(none)`，key 来源 `.env` |
| `--profile 不存在` | `chat --profile missing ...` | **EXIT=2**，stderr：`配置错误: 档案不存在: missing（可用 /profiles 查看，或先 /save 保存）`，无 traceback |
| 档案 JSON 损坏 | `--profiles-file v6verify\bad.json`（内容 `{not valid json`） | **EXIT=2**，stderr：`配置错误: profile store ... is not valid JSON: ...`，无 traceback |
| 仓库本地档案只读使用 | 空 stdin `--no-menu`（默认档案路径，含 last=`deepseek-chat`） | **EXIT=0**；横幅 `profile=deepseek-chat`；仓库 `.rs_agent_profiles.json` 哈希前后未变（只读，不被改写） |

**R5（Low）已在 v7 修复并复验**。v6 曾复现：`--no-profile` + 交互菜单(EOF) 仍出现「① 配置档案」、结束后 `last_profile` 被清空；显式选号还会应用档案。v7 修复（`src/rs_agent/__main__.py`）：`--no-profile` 时菜单分支传 `store=None`（不再出现「① 配置档案」）、`profile_name` 强制 None、菜单后不调用 `store.set_last`；`--profile` 与 `--no-profile` 改为 argparse 互斥。复验（`v7verify/profiles.json`，last=localchat）：

```text
# B1) --no-profile + 交互菜单 EOF
菜单首项为「① 接口协议」（无配置档案步骤）；横幅 profile=(none)；EXIT=0
profiles 文件 SHA256 与 last_profile(localchat) 前后均未变
# B2) --no-profile + 菜单显式输入 "1\n\n\n\n/exit"（不再有档案选择）
无「① 配置档案」；横幅 profile=(none)；EXIT=0；store 未变
# C) --profile localchat --no-profile "hi"
EXIT=2；argparse: argument --no-profile: not allowed with argument --profile；无 traceback
```

v6 的根因（`_run_chat` 菜单分支未判断 `args.no_profile` 且无条件 `store.set_last`）已按上述方式修正。

### 4.3 交互菜单（存在档案）

```text
=== rs-agent 交互配置（直接回车使用默认值）===
① 配置档案
  1) 临时配置（.env / 命令行参数，不写入档案）
  2) localchat（deepseek-flash / chat）
  3) localresp（deepseek-flash / responses）
② 接口协议 ...
③ 模型 ...
④ 思考强度 ...
```

- 输入 `2\n\n\n\n/exit`：选择 localchat，协议/模型/思考强度均取该档案默认值；**EXIT=0**，横幅 `profile=localchat`；
- EOF 取默认：默认档案为 last（`[3] localresp` 时按 last），实际横幅与 last 一致；**EXIT=0**；
- `--no-menu` 跳过菜单（§4.2 已验证）；
- `--no-profile` 时菜单不再出现「① 配置档案」，且不会应用/清空档案（v7 修复，见 §4.2）。

### 4.4 `/use` 运行时切换（保留对话历史）

会话从 last 档案 `localresp`（responses）启动，然后切换：

```text
>>> 请记住数字 42，只回复 OK
OK
>>> /use localchat
（横幅）profile=localchat api=chat ... key=sk-****（来源: 档案 localchat → DEEPSEEK_API_KEY）
已切换到档案 localchat。
>>> 我刚才让你记住的是什么数字？只回答数字
42
>>> /config
profile=localchat endpoint=https://api.deepseek.com model=deepseek-flash api=chat thinking=disabled
key=sk-****（来源: 档案 localchat → DEEPSEEK_API_KEY） profiles_file=v6verify\profiles.json
```

EXIT=0。切换前后分别使用 responses / chat 两种线格式，第二轮仍能回忆第一轮内容，说明 `_rebuild_session` 复用 `Harness.messages` 生效；`last_profile` 随 `/use` 更新为 localchat。`/profiles` 用 `*` 标记当前档案，`/config` 全程脱敏。

### 4.5 两套档案的真实工具调用（chat / responses）

```powershell
uv run python -m rs_agent chat --profile localchat  --profiles-file v6verify/profiles.json "用一句话说说 examples/sample.ppm 的尺寸"
uv run python -m rs_agent chat --profile localresp --profiles-file v6verify/profiles.json "用一句话说说 examples/sample.ppm 的波段"
```

| 档案 | 横幅 | 结果 |
| --- | --- | --- |
| `localchat` | `api=chat profile=localchat`，`key=sk-****（来源: 档案 localchat → DEEPSEEK_API_KEY）` | **EXIT=0**，`[tool] inspect_image` 成功，stdout：`examples/sample.ppm 是一张 2×2 像素的 RGB（PPM）小图。` |
| `localresp` | `api=responses profile=localresp`，同样脱敏 | **EXIT=0**，`[tool] inspect_image` 成功，stdout 给出 2×2 / RGB 三波段 / 0–255 / 均值 127.5 |

### 4.6 回归（一次性、`--no-menu`、`.env`、错误路径）

- 一次性提问（无档案参数、last 已清空）：**EXIT=0**，横幅 `profile=(none)`、`key=sk-****（来源: .env）`；
- `--no-menu` 空 stdin：正常退出，不弹菜单；
- `.env` 自动读取、BOM/行内注释解析（v5 修复）继续有效（由测试与启动横幅覆盖）；
- 错误路径：不可达端点 **EXIT=1** 可读；`--max-steps 0` **EXIT=2**；`--api grpc` **EXIT=2**；均无 traceback；
- 旧 CLI `inspect` / `inspect-raster` 缺失路径回归正常（§3.3）。

## 5. 错误路径证据（v7 复验）

| 场景 | 命令/构造 | 结果 |
| --- | --- | --- |
| 缺 API key | `chat --env-file <不存在> --no-profile "hello"` | **EXIT=1**，`请求失败: Missing API key...`，无 traceback |
| 两个档案 flag 互斥（v7） | `chat --profile localchat --no-profile "hi"` | **EXIT=2**，argparse：`argument --no-profile: not allowed with argument --profile`，无 traceback |
| 未知档案 | `chat --profile missing ...` | **EXIT=2**，`配置错误: 档案不存在: ...`，无 traceback |
| 档案 JSON 损坏/版本不支持/字段非法 | 损坏 JSON、`{"version":99}`、未知字段 | **EXIT=2**，`配置错误: profile store ...` / `档案无效: ...`，无 traceback（另有单测覆盖） |
| 档案 key 环境变量缺失 | 档案 `api_key_env=NOT_SET` | **EXIT=2**，`配置错误: 档案 'x' 需要环境变量或 .env 中的 NOT_SET...` |
| `.env` 中非法 `RS_AGENT_API` | `RS_AGENT_API=grpc` | **EXIT=2**，`配置错误: Unsupported api...` |
| 端点不可达 | `--base-url https://127.0.0.1:9` | **EXIT=1**，`API request failed: [WinError 10061] ...` |
| 非法 key（401） | `--api-key sk-fake-invalid` | **EXIT=1**，HTTP 401 + API JSON 错误体；服务端对 key 脱敏 |
| chat 流缺 `[DONE]` / fatal finish_reason | 解析器级注入 | **LLMError**（v2 修复，回归通过） |
| responses 缺终态 / `response.failed` | 解析器级注入 | **LLMError`（带 `response.failed` 详情） |
| 读流中途 socket 异常 / 纯文本截断 | 注入 `ConnectionResetError` / `--max-tokens 8` | **LLMError: stream interrupted** / stdout 追加“回答可能被截断”提示（R1/R2，v3 修复） |
| `.env` BOM/行内注释 | 临时 `.env`（BOM + ` # comment`） | 正确解析（R4，v5 修复） |

对 v7 全部错误路径捕获文件做 traceback 扫描：0 个含 `Traceback`（历史 v1 取证文件除外）。

## 6. verifier 新增测试：`tests/test_cli_smoke.py`

该文件由 verifier 新增（7 个用例，不依赖真实网络）。v6 中 Lead 将“缺 key”用例补充 `--no-profile`，以隔离仓库本地 `.rs_agent_profiles.json` 的影响、保持确定性。覆盖点：`chat` 子命令与参数解析、`--api` 非法值、无 key 退出码 1、`--help` 选项；子进程用例使用 `sys.executable -m rs_agent`、清理 key 环境变量、UTF-8 捕获输出。

## 7. 遗留问题清单（v7 最终状态）

| 编号 | 严重级别 | 问题 | 修复 / 证据 | 状态 |
| --- | --- | --- | --- | --- |
| R1 | ~~低~~ 已修复（v3） | 读流阶段 socket 异常未包装成 `LLMError`，CLI 会打栈 | v3 捕获 `urllib.error.URLError`/`OSError` → `LLMError("stream interrupted: ...")`；v7 全量测试仍通过（`test_read_errors_are_wrapped_as_llm_error`） | **已修复** |
| R2 | ~~低~~ 已修复（v3） | 纯文本生成被截断时无提示 | v3 在 stdout/assistant 消息追加截断提示；v7 全量测试仍通过（`test_truncated_text_without_tool_calls_gets_notice`） | **已修复** |
| R3 | 信息 | 无重试/退避/断线续传；`timeout` 固定 120s 且未暴露 CLI 参数；`--max-tokens` 负数、`--temperature` 越界未本地校验（API 返回可读 400） | `docs/minimal-harness-mvp.md` 明确列为刻意不做的范围 | 保留（信息项） |
| R4 | ~~信息~~ 已修复（v5） | `.env` 的 UTF-8 BOM 与行内注释解析边界 | v5 改为 `utf-8-sig` + 未加引号值按 ` #` 截断；v7 全量测试仍通过（`test_load_env_file_handles_bom_and_inline_comments`） | **已修复** |
| R5 | ~~低~~ 已修复（v7） | v6 中 `--no-profile` 在**交互菜单**路径被忽略：菜单仍显示「① 配置档案」且可选中档案（横幅变为该档案），与 README/help“忽略所有档案”矛盾；EOF/临时配置选择还会把持久化的 `last_profile` 清空 | v7 修复：`--no-profile` 时菜单分支传 `store=None`（无「① 配置档案」）、`profile_name` 强制 None、菜单后不 `store.set_last`；`--profile`/`--no-profile` 改为 argparse 互斥。复验：菜单首项为「① 接口协议」、横幅 `profile=(none)`、store 的 SHA-256 与 last 前后未变；两 flag 同用 exit 2 可读；新增 3 个测试锁定 | **已修复** |

v7 已无低严重度以上遗留项；R1/R2（v3）、R4（v5）、R5（v7）修复后各有回归测试锁定，仅 R3 作为信息项保留（文档声明的刻意范围），不影响 MVP 交付。v8 为低风险维护改动（`getdata` 弃用替换 + 回归测试），**不新增遗留项**，harness 行为不变。v9 为 PR #8 Codex review 的 P1/P2 修复，**不新增遗留项**（已修复并有 3 个回归测试锁定），R1–R5 状态不变。

## 8. 与项目文档的一致性

- `README.md`：新增「多套配置切换：配置档案 + 斜杠命令」章节，说明 `.rs_agent_profiles.json`（gitignored）、只记录 `api_key_env`、斜杠命令表、`--profile/--no-profile/--profiles-file`、last 自动沿用、优先级「CLI 参数 > 档案 > 环境变量/`.env` > 默认」——v7 修复后与实现完全一致；
- `docs/minimal-harness-mvp.md`：新增「配置档案」章节，说明 `Profile`/`ProfileStore`、JSON 结构、`last_profile`、原子写、`/use` 保留历史、优先级；并明确 `api_key` 字段仅用于手工本机档案（`/save` 从不写明文）；
- `AGENTS.md` / `.env.example` / `.gitignore` 已同步档案相关说明与忽略规则；
- `docs/deepseek-api-notes.md` 与 `docs/dsh-minimal-harness-notes.md` 的协议/工具回传结论继续成立（v7 未改协议层）。

## 9. 范围与密钥合规

- verifier 本轮只修改 `docs/harness-verification.md`，未改 `src/` 与 `tests/`；
- 仓库本地 `.env`（真实 key）与 `.rs_agent_profiles.json`：验收前后 SHA-256 未变（未被修改/提交）；验收中仅只读使用仓库档案文件；
- 测试档案写在仓库内临时目录（v6 为 `v6verify/`、v7 为 `v7verify/`、v9 为 `v9verify/`；因本机 Python 子进程不可写 `%TEMP%` 根目录），验收后删除；其中 JSON **不含明文 key**；所有捕获输出与 JSON 的“真实 key 子串”扫描结果为空；
- 横幅、`/config`、`/profiles` 一律脱敏为 `sk-****`；`/save` 只写 `api_key_env`；
- `.env` 与 `.rs_agent_profiles.json` 均在 `.gitignore` 中；`git diff --check` 通过。

## 10. 复现命令汇总

```powershell
$env:PYTHONIOENCODING = 'utf-8'
# 仓库根目录放好 .env（内容不打印）；档案测试用独立 --profiles-file 避免污染本地档案

uv sync --locked
uv run pytest -q
uv run pytest -q -W error::DeprecationWarning          # v8：无 Pillow 弃用警告
uv run pytest tests/test_tools.py -W error::DeprecationWarning -q

# 档案读写（仓库内临时路径；不要用 %TEMP% 根）
"/save localchat`n/config`n/profiles`n/exit`n" |
  uv run python -m rs_agent chat --no-menu --profiles-file v7verify/profiles.json

# 切换档案并保留历史
"请记住数字 42，只回复 OK`n/use localchat`n我刚才让你记住的是什么数字？只回答数字`n/exit`n" |
  uv run python -m rs_agent chat --no-menu --profiles-file v7verify/profiles.json

# 启动参数与 last 记忆
uv run python -m rs_agent chat --profile localchat --profiles-file v7verify/profiles.json "只回答 OK"
$null | uv run python -m rs_agent chat --no-menu --profiles-file v7verify/profiles.json
uv run python -m rs_agent chat --no-profile --profiles-file v7verify/profiles.json "只回答 OK"
uv run python -m rs_agent chat --profile missing --profiles-file v7verify/profiles.json "hi"   # → 2
uv run python -m rs_agent chat --profile localchat --no-profile "hi"                           # 互斥 → 2

# 菜单（存在档案；--no-profile 时不出现「① 配置档案」）
"2`n`n`n`n/exit`n" | uv run python -m rs_agent chat --profiles-file v7verify/profiles.json
$null | uv run python -m rs_agent chat --no-profile --profiles-file v7verify/profiles.json

# 两套档案真实 E2E
uv run python -m rs_agent chat --profile localchat  --profiles-file v7verify/profiles.json "用一句话说说 examples/sample.ppm 的尺寸"
uv run python -m rs_agent chat --profile localresp --profiles-file v7verify/profiles.json "用一句话说说 examples/sample.ppm 的波段"

# v9 P1：菜单选 chatp 后 /use respp，横幅应为 respp 的 responses/v4-pro/enabled(high)
"2`n`n`n`n/use respp`n用一句话说说 examples/sample.ppm 的波段`n/exit`n" |
  uv run python -m rs_agent chat --profiles-file v9verify/profiles.json
# v9 P2：last=respp 时菜单选「临时配置」，协议/模型/思考默认应来自 .env（chat/flash/disabled）
"1`n`n`n`n用一句话说说 examples/sample.ppm 的尺寸`n/exit`n" |
  uv run python -m rs_agent chat --profiles-file v9verify/profiles.json
```

## 11. 最终结论

**通过（PASS），无阻塞项。** v9 基线（commit `c585cb6`，PR #8）**104/104 测试通过**：PR #8 的 Codex review P1（菜单答案污染 `/use` 配置）与 P2（选「临时配置」后默认值仍来自旧档案）均已修复，独立真实 CLI 复验通过——`/use` 后横幅/真实调用使用新档案的协议、模型、思考强度；真实 CLI flag 仍优先；菜单选临时配置时默认值来自 `.env` 而非旧档案。配置档案、双向言工具循环、`.env`、错误路径等 v7/v8 验收结论继续有效；R1/R2/R4/R5 保持已修复，仅 R3（无重试/超时不可配）作为信息项保留，属文档声明的刻意范围，不影响交付。
