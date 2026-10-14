# SecurityAdaptiveMAS

**面向长任务的安全感知自适应多 Agent 组织**

用户的任务会不断收到新消息和工具结果。这个项目研究：怎样按当前任务需要调整分工、结果交付和工具权限；遇到可疑外部内容时，怎样保留完成任务所需的信息，并记录调整带来的成本。

> **当前阶段：v0.5 工程链路与离线验收。** 统一入口复用原有 Controller、GraphRuntime 和 ARE 执行链。没有新的付费 DeepSeek 实验，没有测出安全收益或 Token 节省。
>
> [架构](docs/architecture.md) · [算法与状态口径](docs/algorithm.md) · [命令手册](docs/cli.md) · [实验与历史证据](docs/experiments.md) · [开发指南](docs/development.md) · [研究日志](docs/research-log/README.md)

## Quickstart（Windows PowerShell）

在仓库根目录操作。基础 CLI 只需 Python 3.10+ 与 PyYAML；真实运行另需已有的 ARE/MANTA 源码、模型依赖和 Gaia2 文件系统。`doctor` 会报告本机缺少什么，不会自动下载或安装。

```powershell
python -m pip install -e .
security-mas --help
python -m security_adaptive_mas --help
security-mas doctor --config configs/research_v0_4.yaml
security-mas run --design-config configs/research_v0_4.yaml --mode adaptive --scenario scenario_universe_22_kb5ah9 --dry-run
```

已有项目虚拟环境时，用 `.\.venv\Scripts\python.exe` 代替 `python`，或先运行 `.\.venv\Scripts\Activate.ps1` 激活它。安装依赖可能联网；本轮本机验收使用了缓存与离线安装选项，见[实施报告](docs/reports/v0_5_implementation_report.md)。从零配置 ARE 的步骤和限制见[开发指南](docs/development.md)。

以上检查不调用模型、不推进任务。`configs/research_v0_4.yaml` 是继续使用的设计配置名；v0.5 主要规范入口和状态，不另造一套配置。

### 只生成实验计划

```powershell
security-mas experiment plan --design-config configs/research_v0_4.yaml --output results/plans/development.json
```

默认生成 20 个开发任务 × 7 种方法，共 140 条 `NOT_RUN` 计划。40 个冻结评测 ID 仍保留；任务清单不代表已经运行，也不代表攻击入口已逐项验证。输出文件已存在时会报错，请给新文件名。

### 汇总已有结果

```powershell
security-mas report --runs results/v0_3_pilot --output results/v0_5_pilot_report.json
```

这是本机已有历史目录的示例，代码克隆不包含 `results/`。汇总器只读取实际文件；缺失内容保持 `unknown`，不会把官方 success 当作完整任务完成，也不会改写原始结果。[全部参数与付费入口](docs/cli.md)另见命令手册；付费执行必须显式指定数量、总费用上限并确认模型费用。

## 输入怎样变成输出

```text
CLI / scripts
  → api.run → 原有 run_framework
  → ARE 已到达的 USER / ENV / 工具结果
  → 任务观察与分析、轻量触发、风险记录
  → Controller 生成并选择有限 RewritePlan
  → GraphRuntime 提交一张实际生效的组织图
  → FrameworkTeam 按角色顺序执行 → ToolGateway → ARE 工具
  → trace / topology_events / summary / evaluation / runner.log
  → report 只读汇总
```

角色可以有不同步骤、输入、工具子集和输出字段。13 类拓扑生成族仍保留；实际执行是顺序的。图预览不会派工，只有正式提交的图才影响执行。模型提出的任务和字段不能增加权限，外部数据不能改写用户目标。

## v0.5 主要改了什么

| 改动 | 现在怎样处理 |
|---|---|
| 产物检查与任务完成分开 | 字段和证据 ID 合格仅记 `artifact_schema_valid`；内容正确和整个任务完成没有独立判据时仍为 `unknown`。 |
| 已有信息与未来计划分开 | `satisfied` 表示结果已可读取；`planned` 表示有后续获取计划，敏感动作不能把它当作已交付结果。 |
| 风险含义写清楚 | 分开记录低可信来源的结构潜在可达性和已登记可疑来源的可达性。没有报警不等于零风险。 |
| 不奖励组织复杂度 | 不再因为交付更多对象或增加协议轮次而加分；比较必要信息、可比风险、工作量代理和稳定顺序。 |
| 统一使用入口 | 新 CLI、`python -m`、scripts 和旧入口共用原有运行器，预算、网关和评分不另实现一份。 |

`declared_work` 只是声明步骤和字符等代理量，不是节省的 Token。实际 Token、费用估算和耗时从运行记录读取。没有充分真实数据时，`empirical` 不会生成假系数。

## 已有证据和不能下的结论

- [官方 Single Agent 五场历史日志](docs/research-log/2026-10-13.md)：`ScenarioRunner.success` 为 **5/5**，三场出现过 `MaxIterationsAgentError`。这些运行没有使用后来新增的 Controller。
- [v0.2 真实邮件案例](docs/reports/framework_v0_2_paid_22_kb5ah9_2026-10-14.md)和 [v0.3 受攻击 Single Agent 对照](docs/reports/v0_3_attacked_single_comparison_22_kb5ah9_2026-10-14.md)保留当时数字和停止原因；它们不是 v0.5 实验。
- [v0.4 实施报告](docs/reports/v0_4_implementation_report.md)和 [v0.5 实施报告](docs/reports/v0_5_implementation_report.md)记录代码与非付费验收。离线测试不能证明实际攻击成功率、合法任务完成率或并行加速。

## 仍然缺什么

独立的内容和完整任务验收、真实成本校准、AgentDyn 工具能力目录、ARE 并发与完整状态恢复仍有 OPEN / BLOCKED。当前仅复用已核验的 `22_kb5ah9` 邮件入口；四类攻击标签不是四类已验证攻击集。下一步先 Review 当前链路，再决定是否授权一次有限的真实模型验证。

完整数据放在 [Gaia2 Release](https://github.com/ruihanzhu5-cyber/SecurityAdaptiveMAS/releases/tag/gaia2-data-78ea3bd)，Git 保留五个历史开发场景。目录、校验和下载命令见[数据说明](data/gaia2/README.md)。旧阶段文档保留在[归档索引](docs/archive/README.md)；本轮不改写研究日志和历史实验数字。
