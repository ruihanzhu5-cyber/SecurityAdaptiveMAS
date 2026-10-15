# 当前系统架构（v0.5）

v0.5 规范的是工程入口，沿用 v0.4 角色化组织。本文描述实际代码，不代表完成论文实验。

## 单一调用链

```text
security-mas / python -m security_adaptive_mas / scripts
  → cli.main → api.run
  → run_framework.main(argv, design_override=...) → run_case
  → 官方 ScenarioRunner / ARE 默认 Coordinator
     ↔ are_bridge：实际通知、输入和工具边界
     ↔ Controller：观察、任务提案、触发和选图
       → joint_rewriter：生成、预览、评估、选择 RewritePlan
       → GraphRuntime：版本、分工、路由、对象 ACL 与生命周期
       → FrameworkTeam：实际角色调用、工具读取和产物交付
         → ToolGateway → 同一 ARE 应用环境
  → 原有共享 RunBudget / trace / 结果文件
  → cli.report：只读已有运行记录
```

CLI 不另建 ScenarioRunner，不通过子进程伪装集成。`experiment run` 只是逐条把冻结方法/seed 转为同一运行器的参数，并使用同一输出账本。`doctor` 和 `experiment plan` 不执行环境或模型。

## 模块各有什么用

| 职责 | 实际位置 | 边界 |
|---|---|---|
| 记录已发生事件并维护当前任务 | [online_observation.py](../security_adaptive_mas/online_observation.py)、[online_task.py](../security_adaptive_mas/online_task.py) | 不把未来通知或官方答案给 Controller。 |
| 提出具体角色和信息需求 | [task_proposer.py](../security_adaptive_mas/task_proposer.py)、[organization_contracts.py](../security_adaptive_mas/organization_contracts.py) | 模型提案需要 Python 检查来源；不构成授权。 |
| 决定何时重新分析和选图 | [controller.py](../security_adaptive_mas/controller.py)、[performance_monitor.py](../security_adaptive_mas/performance_monitor.py) | 额外模型请求计入同一预算。 |
| 比较有限候选 | [joint_rewriter.py](../security_adaptive_mas/joint_rewriter.py)、[graph_evaluation.py](../security_adaptive_mas/graph_evaluation.py)、[cost_model.py](../security_adaptive_mas/cost_model.py) | 状态、风险和工作量代理分开。 |
| 真正影响执行 | [graph_runtime.py](../security_adaptive_mas/graph_runtime.py)、[dynamic_team.py](../security_adaptive_mas/dynamic_team.py) | 图提交之后才派工、交付、变更权限。顺序执行。 |
| 工具与费用边界 | [security.py](../security_adaptive_mas/security.py)、[deepseek.py](../security_adaptive_mas/deepseek.py) | 权限、图版本、预算和真实副作用检查保留。 |
| 接环境与存结果 | [are_bridge.py](../security_adaptive_mas/are_bridge.py)、[run_framework.py](../security_adaptive_mas/run_framework.py) | 复用官方导入器、Runner 和已有邮件后果评分。 |

角色历史、来源关系和信息需求保存在图旁。研究记号是一张组织图 `G_t`，不是把旧三图概念图当作已实现算法。

## 输入与状态边界

只有已到达 USER 更新用户原始目标。ENV、邮件正文、工具返回和 Worker 产物可作为资料，不能直接改写 root 或提升工具能力。结构合格的资料可继续流转，但不能由格式检查推导内容正确或整个任务完成。

## 输出

每次真实调用仍由旧运行器生成唯一运行目录，保存配置/代码哈希、框架轨迹、拓扑事件、summary、evaluation 和日志。官方 success、异常、预算停止、注入投递、模型接触、操作尝试与最终邮箱状态各自记录。详见[实验口径](experiments.md)。

## OPEN / BLOCKED

新 v0.5 尚无真实 DeepSeek 任务结果。AgentDyn 接入、真实并行、完整快照恢复、独立语义与合法完成判据仍未解决；不影响本轮对统一入口和本地只读工具的验收。具体证据见[实施报告](reports/v0_5_implementation_report.md)。
