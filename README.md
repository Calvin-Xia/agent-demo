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

仓库目前处于初始化阶段，尚未提供可运行代码。

第一个里程碑是实现一个无需 API Key 的离线 NDVI tool-call hello-world，用结构化轨迹展示：

```text
task → decision → action → observation → final answer
```

该示例只用于验证 Agent 框架、工具接口和项目安装流程，不代表完整的 LLM Agent 或正式遥感实验。

## 路线图

- [ ] 建立 Python 与 `uv` 项目骨架；
- [ ] 实现离线 NDVI 工具调用示例；
- [ ] 添加 smoke test 和持续集成；
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
