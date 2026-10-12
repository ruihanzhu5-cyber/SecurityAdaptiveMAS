# Gaia2 在线任务分析与拓扑候选：Phase 1 实现记录

更新：2026-10-12。此页描述代码现状，不报告 Gaia2 在线性能结果。

## 本次核验的代码边界

- `are_adapter.py` 用官方 `JsonScenarioImporter` 载入完整 Scenario，供 ARE 环境调度与官方 `Scenario.validate` 使用。Runner 在 `oracle=False` 下执行；完整 Scenario 仍在 ARE 进程中，**不能**把它交给在线规划器。
- `are_bridge.py` 已有官方 `AgentBuilder` 接缝、只读工具包装、`ObservedContext`、来源记录和 Tool Gateway。本次在 `get_notifications()` 已按当前 ARE 时间取出通知之后记录 USER/ENV 内容；工具实际调用成功后记录动作和结果证据 ID。Planner 入口不接收 Scenario、Oracle DAG、Judge 或最终轨迹。
- 旧 `organization.py` 的 `TaskAnalyzer` 仍是阶段/子任务文本包装，`OrganizationSelector` 仍按手工权重在 Singleton/Star/Chain 间打分。它是保留兼容的旧基线，不是论文方法。本次没有让新候选自动替换旧 Controller 执行路径。
- `dynamic_team.py` 的现有 ARE 路径是一个受限的、只读的双 Worker 顺序调用和 Verifier 路由。`Controller` 能发布 MANTA spec，不等于 ARE 已按任意候选图并行执行。现有 Gateway、授权与写入去重保持原状。

## Controller 在时刻 t 能看见什么

`OnlineObservationRecorder` 只接受四类运行时输入：已交付的 USER/ENV 通知、实际发起或完成的工具动作、已观察到的工具返回。通知由 ARE 原有的按时间取消息函数交付；尚在队列中的未来通知不会被复制到快照。普通 ARE 工具在 Gateway 调用成功后记录动作和返回；动态团队工具在开始时记录委托动作，Worker 工具成功后记录读动作和返回。动作记录本身不代表整个任务成功。工具原文保留在既有 `ProvenanceStore`，在线规划快照只带工具名和证据 ID，减少不必要的原始内容复制。

不可变 `OnlineObservation` 包含任务可见描述、事件序号、四类事件、当前 Controller 拓扑版本、工作状态、预算以及证据 ID。没有足够事实时，工作状态和预算显式为 `UNKNOWN`。真实 `RunBudget` 已连接到 Controller 时，快照读出剩余请求数、实际已用 Token 对应的余额和估算费用余额。尚未从 ARE 自动推导已完成/运行/待执行工作列表；该字段保持 `UNKNOWN`，等待可靠的运行时事件来源。

**信任边界：** ARE 可以持有完整 Scenario 判分；`record_are_delivery` 的参数只能是 ARE 此次真正交付的通知。`OnlineTaskAnalyzer` 只接收快照，`CandidateGenerator` 只接收 `TaskAnalysis`。这条类型与调用链不提供 Oracle/未来事件入口。它不证明整个进程没有其他代码能读 Scenario；若将来接入新 Planner，仍须保持这条显式参数边界并做运行时审计。

## 在线任务图 D_t

`online_task.py` 提供 `Subtask`、`TaskDependency`、`InformationRequirement`、`TaskStatus`、`TaskAnalysis` 和 `Certainty`。依赖分为 `CONFIRMED`、`INFERRED`、`UNKNOWN`；确认或推断的边必须引用当前快照中可见的证据。验证器检查任务 ID、证据、边端点和已知依赖的环。

目前验证器只能核对“证据确实已在线可见”，不能自动证明提取器所标的 `CONFIRMED` 一定正确；确认标准和证据复核流程仍需定义。

当前 `OnlineTaskAnalyzer` 是保守基线：可见 USER 请求形成一个根工作项；新 ENV 通知只进入“含义尚未分析”的证据列表，**不会被自动编造成子任务**。只有调用方给出引用已到达证据的 `TaskUpdate`，才会新增子任务、信息需求或确认/推断依赖；Controller 会先用图验证器检查再保存。测试用模拟提取结果证明 D_t 可以随事件更新，真实 ARE 路径目前还没有这样的提取器。并行度、通信需求和关键路径仍是未知值。后续 LLM/启发式提取器可以替换它，但输出必须通过同一证据和图验证器。论文所讨论的通信收益、架构开销只是选择研究特征的动机，不是本仓库固定阈值的依据：参见 [Rizvi-Martel 等](https://arxiv.org/abs/2510.13903) 与 [Orogat 等，MAFBench](https://arxiv.org/abs/2602.03128)。

## 统一拓扑候选与可替换接口

`TopologyCandidate` 明确记录 Agent/角色、任务分配、有向通信边、必要的信息交付关系、来源、规划证据、协议和运行时支持级别。`template` 只是来源说明；自定义图可无模板。Debate/Voting 是需要额外交互协议的模式，不把它们冒充为通信图的名称。基础模板当前可产出 Singleton、Star、Chain、Fully Connected、Tree 的**结构候选**。

接口分开为 `OnlineTaskAnalyzer`、`CandidateGenerator`、`TopologyValidator`、`PerformanceEvaluator` 与 `TopologySelector`。`MantaCandidateGenerator` 可把旧 MANTA kernel planner 的三个结构转成统一候选；`candidate_from_manta` 是单个 spec 的适配函数。未来的 Task-Feature-Guided Planner 可以实现相同 `generate(TaskAnalysis)` 契约，无需修改 Controller 的快照格式。

`TopologyValidator` 检查 Agent 数、授权工具、重复或非法边、所有可见任务的分配、信息交付边、证据和协议。默认只准 `planning_only`；`controller_spec_only` 需调用者明确开启。`BaselinePerformanceEvaluator` 只输出 Agent 数和通信边数，任务收益为 `None`。`TopologySelector` 在收益未知时不自行猜分：有当前候选则保持当前候选，否则返回 `None`，等待后续估计器或人工实验配置。本阶段没有最终性能优化算法。

| 支持级别 | 实际含义 |
|---|---|
| `planning_only` | 图结构已生成和静态验证；没有部署到 ARE |
| `controller_spec_only` | 可对应旧 MANTA spec；Controller 可持有 spec，不代表 ARE 任意图执行 |
| `are_single_read_only` | 仅对现有单 Agent 只读 ARE 路径可主张；本次生成器不自动标记 |
| `are_dynamic_read_only` | 仅对现有受限双 Worker 工具路径可主张；本次生成器不自动标记 |

本次测试没有调用付费模型，也没有运行官方 Gaia2 在线任务或 Judge。因而没有可报告的在线任务完成率、并行加速或多拓扑运行结果。

## 数据集划分和防泄露流程

五个现有候选场景及其 SHA-256 保存在 `configs/five_manifest.json`；Gaia2/ARE 版本和 Parquet 哈希保存在 `configs/source_versions.json`。这些场景此前经过人工静态筛选和 Oracle 内容检查，所以 `configs/evaluation_split.json` **全部列为 Development**，Held-out 列表为空。不能把这五个场景重新称为未见过的测试集。

正式评价前应另选未人工查看 Oracle 的官方场景并记录 ID、来源 revision、内容哈希和划分；先冻结 Planner 代码提交、Prompt、规则、候选集、参数、授权与预算。Development 可以查看全轨迹并调试；Held-out 运行时只给同一在线观察接口，结束后才用 Oracle Events、Oracle DAG、官方 Judge 和完整轨迹做明确标注的诊断。Oracle DAG 是官方行为参照，**不是最优多智能体拓扑标签**。任何测试集信息若参与调参，须移回 Development 并另选新的 Held-out。

## 验证与待解决问题

离线测试覆盖：ARE 官方消息队列中未来通知变化不影响当前快照；Oracle/Judge 不在规划对象结构内；新环境事件经显式、带证据的模拟提取更新 D_t；未见证据、任务依赖环、非法 Agent/边/任务、未支持协议和无运行时支持的候选被拒绝；旧 Controller/Gateway/ARE Bridge 测试继续运行。运行命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests
```

仍需完成：从真实 ARE 工作流提取可靠的任务完成/进行中状态；对通知和工具调用做事件级时间戳与预算一致性审计；让结构候选经真实 ARE 调度器执行，并核验实际并行/通信；建立无 Oracle 的开发轨迹和冻结后 Held-out 评价；研究可替换的任务特征提取及性能估计。当前在线观察和候选合同属于**工程研究基础**。未来若能提出并验证比强基线更好的任务驱动候选生成或选择机制，才可能成为论文方法；本次没有这样的实验结论。
