# multitemporal-rs-agent-demo

面向多时相遥感影像分析的 Agent 实验仓库。

本项目围绕“大语言模型智能体的工具调用策略与任务分解能力”展开，目标是在多时相遥感影像分析场景中，构建一个简单、透明、可复现的 Agent 框架，并逐步比较不同工具组织与任务分解策略的实际表现。

## 研究目标

- 构建可观察、可测试的最小 Agent 执行循环；
- 接入遥感影像读取、指数计算、变化分析等基础工具；
- 比较显式工具集、工具预筛选与按需工具发现等调用策略；
- 研究复杂遥感任务的分解、执行、验证与纠错过程；
- 建立可复现的实验任务、评价指标与结果记录方式。

## 当前阶段

仓库目前已提供第一条可运行的最小离线 Demo。

第一个里程碑是一个无需 API Key 的 `inspect_image(path)` 单步 Agent，用结构化轨迹展示：

```text
task → decision → action → observation → final answer
```

该示例使用固定的 scripted policy，每次都调用 `inspect_image`，只用于验证 Agent 轨迹、工具接口和项目安装流程。它不包含 LLM、GeoTIFF 专用信息或真正的多时相分析。

## 运行最小 Demo

安装锁定环境、检查仓库内示例图片并运行测试：

```bash
uv sync --locked
uv run python -m rs_agent inspect examples/sample.ppm
uv run pytest -q
```

命令输出 JSON 形式的完整五阶段轨迹：`task` 记录输入任务，`decision` 展示固定策略的工具选择，`action` 记录调用参数，`observation` 保存工具结果，`final` 给出受控的最终状态和答案。

代码、数据流、测试与失败收束的完整拆解见 [`docs/minimal-image-agent-tool-walkthrough.md`](docs/minimal-image-agent-tool-walkthrough.md)。

## 路线图

- [x] 建立 Python 与 `uv` 项目骨架；
- [x] 实现离线 `inspect_image(path)` 单步 Agent；
- [x] 添加确定性单元测试；
- [ ] 添加持续集成；
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
