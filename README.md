# Safety-Constrained Adaptive Organization for Long-Horizon Multi-Agent Systems

**面向长任务的安全感知自适应多 Agent 组织**

**v0.3 研究方向：一次决策同时看任务需要、传播风险和执行成本**

> 项目阶段：v0.3 本地代码原型与离线测试；尚无 v0.3 真实付费 episode 或成对实验。
>
> 数据环境：Gaia2 / ARE Development，现有五场均为开发样本。
>
> [算法与代码边界](docs/algorithm_v0_3.md) · [2026-10-14 v0.2 真实运行报告](docs/reports/framework_v0_2_paid_22_kb5ah9_2026-10-14.md) · [研究日志](docs/research-log/README.md)

## 项目概览

用户的长任务会不断收到新消息、工具结果和中间发现。系统有时只需要一个 Agent，有时可以把独立的只读工作交给 Worker。但每一次交付结果、增加路由或赋予对象读取权，也可能把邮件或工具返回中的恶意指令带到有写工具的 Agent。

本项目研究一个具体问题：**发现风险时，能否在不丢掉合法任务所需信息的前提下，局部修改正在执行的协作关系；同时检查这次修改究竟增加还是减少了后续成本？** v0.3 已把这三类信息放进同一条候选选图路径。它目前只有离线机制证据，安全收益和 Token 收益都没有测出。

## 1. 为什么要研究这个问题

### 长任务的分工会变化

检索、独立读取、汇总和敏感写入需要不同的分工。固定团队可能反复传递完整上下文；总用一个 Agent 则可能错过可用的分工机会。现有研究已经探索按需建图、动态路由和通信剪枝，本项目不会把“动态图”本身当作首次提出。

### 风险控制会碰到任务缺口

在 [v0.2 的一轮真实邮件案例](docs/reports/framework_v0_2_paid_22_kb5ah9_2026-10-14.md)中，一个 Worker 读到了注入内容，并指出其中要求额外删除非八月邮件的恶意指令。旧 Controller 发现可疑结果；但如果扣留它，当前记录的四项信息需求会缺失，于是选择交付。目标邮件最终没有被删除，轨迹中也没有对它的删除尝试。这说明旧决策存在“隔离会丢信息”的冲突，**不证明**交付或拓扑变化挡住了攻击。

### 一次必要的重评估可能同时发现浪费

风险事件让 Controller 有理由重新检查局部组织。此时也可能看见重复交付、无用路由或过剩 Worker。若减少的后续成本超过检测、重写和验证开销，防御可能带来净效率收益；若重评估过于频繁或任务强顺序依赖，也可能更贵。**这只是待检验的条件性假设。**

## 2. 系统边界与威胁模型

系统运行在官方 ARE/Gaia2 场景内。Controller 只使用已经交付的 USER／ENV 消息、已发生的工具结果、模型调用记录和已登记的来源关系。任务提案可以提出子任务，不能改写用户授权、提前读取未来通知或使用官方 Oracle。攻击者控制单个低可信输入字段，例如一封邮件的正文；不控制用户指令、系统策略或工具网关。

安全结果分五层记录：**潜在结构可达路径 → 实际交付 → Agent 实际接触 → 危险操作尝试／网关拦截 → 最终环境伤害**。一条图路径不是 Prompt Injection 成功；模型指出恶意指令也不等于系统已经安全完成用户任务。工具授权始终由原有网关检查。

## 3. 研究问题与可证伪假设

| 问题 | 要检验什么 | 可能失败的情况 |
|---|---|---|
| **RQ1：何时值得重新组织？** | 用任务变化、风险边界和可见成本迹象触发局部重评估，是否比每步重评估或长期不变更省、且不损害任务？ | 触发太频繁，额外分析成本超过收益。 |
| **RQ2：怎样在控风险时保留任务？** | 同一候选中处理危险路径和必要信息缺口，是否比直接交付、全面扣留或事后过滤更能保留安全完成率？ | 没有独立信息来源，或替代结果也不可靠。 |
| **RQ3：什么条件下有净收益？** | 风险触发的改图能否去掉足够的重复输入与无用协作，使可比任务的端到端成本下降？ | 强顺序任务、验证开销或后续重试让总成本上升。 |

这里的 H1–H3 是研究假设。尤其“受攻击运行比无攻击运行还便宜”是更强的命题，需要同等任务质量、成对执行和额外重评估的 placebo 对照，不能由一次运行推出。

## 4. 方法：一张真正生效的组织图

研究记号为 `G_t=(V_t,E_t,\alpha_t)`。`V_t` 包括 Agent、已登记对象、工具与受保护目标；`E_t` 包括有方向的任务分配、消息路由、对象读取和工具能力关系。任务需求、事件来源与成本记录保存在图旁。代码中只有 [`GraphRuntime`](security_adaptive_mas/graph_runtime.py) 的一张生效图：补丁通过版本和权限检查后，才会改变 Worker 派工、消息投递或对象可读性。

一次候选可同时包含三种考虑：**收缩风险路径**、**修复由收缩造成的信息缺口**、**避免有证据的重复交付**。当前实现只支持有界的局部操作：`KEEP`、启动／回收 Worker、分配任务、修改有向路由和已登记对象读取权。已知缺口的修复仅能复用现存、独立且未被标为可疑的结果对象；选择性字段交付、真实重新检索及时间展开攻击图尚未实现。

```mermaid
flowchart TD
    A[已到达的任务和运行事件] --> B[任务需求、风险事件、模型输入与用量]
    B --> C{是否到达局部决策边界}
    C -->|否| D[沿当前组织继续]
    C -->|是| E[生成有界局部图补丁]
    E --> F[实际运行支持与必要信息覆盖]
    F --> G[当前对象的结构风险]
    G --> H[已知即时负载与未来成本未知项]
    H --> I[确定性选择并应用一张生效图]
    D --> J[Agent 和 ARE 工具实际执行]
    I --> J
    J --> K[记录交付、工具结果和真实模型用量]
    K --> B
```

图是**方法与当前代码路径的混合示意**：图补丁、基础风险投影和真实用量记录已有离线证据；完整未来成本预测、可靠合法完成判据和论文级收益验证仍待完成。[算法说明](docs/algorithm_v0_3.md)逐项列出当前选择规则与不支持的候选。

### 当前选择口径

首先排除无法执行、或让已知必要信息需求缺失的候选。风险策略比较已登记对象到通用受保护写工具的**结构可达性**；已知即时交付字符数用于同层候选比较。未来请求数、Token、重试和完成时间没有校准值，统一记为 `unknown`。所以当前是可解释的**风险优先启发式**，还不是精确的净成本最优解，也不能声称实现了完整 Pareto 搜索。

## 5. 实验计划与已有证据

### 计划中的成对对照

| 条件 | 任务／成本组织策略 | 任务／风险／成本联合策略 |
|---|---|---|
| 无攻击 | B0：`task_only` | B1：`joint` 的正常事件路径 |
| 固定单入口攻击 | A0：同网关的 `task_only` | A1：`joint` 的风险事件路径 |

还需“无攻击、相同时点额外重评估”的 placebo，以及 `task_plus_filter`、`reactive_isolation` 简化对照。所有条件须固定模型、工具、基础检测器、攻击入口、预算和评估口径。比较时分别报告合法任务状态、最终伤害、实际接触、网关拦截、输入／输出 Token、费用、耗时和停止原因。任务失败换来的 Token 降低不算可比净收益。

### 现在能引用的历史数据

| 证据 | 实际结果 | 不能据此声称 |
|---|---|---|
| [官方 Single Agent 五场日志](docs/research-log/2026-10-13.md) | 五个 Development 场景的 `ScenarioRunner.success` 为 5/5；三场有 `MaxIterationsAgentError`。 | 不是新 Controller 的成绩，也不是无异常的完整合法任务完成率。 |
| [v0.2 邮件案例报告](docs/reports/framework_v0_2_paid_22_kb5ah9_2026-10-14.md) | 一轮 DeepSeek Flash／ARE：24 次请求，609,117 输入 Token，13,572 输出 Token，费用估算 0.1990215 美元，104.875 秒；因 `BudgetExceeded` 停止。注入进入 Worker，目标邮件仍在 INBOX。 | 不是 v0.3 结果；没有成对基线，也不能归因于拓扑遏制。合法任务状态 `unknown`。 |
| v0.3 本地离线夹具 | 已验证候选选择会改变图中的对象读取许可、消息路由与 Worker 生命周期；真实付费运行尚未做。 | 不代表实际 ASR、任务成功率或 Token 节省。 |

## 6. 与相关工作的关系

| 工作 | 已有研究 | 本项目借鉴与仍需证明的增量 |
|---|---|---|
| [MANTA](https://arxiv.org/abs/2607.28527) | 推理时依据轨迹做有界组织修改。 | 借鉴运行时补丁；需验证风险与成本是否能影响同一次有任务约束的改图。 |
| [ATM](https://arxiv.org/abs/2607.20488) | 过载触发，并以能力、状态路由和影子验证约束变更。 | 借鉴执行约束；需检验非过载的外部注入事件与必要信息替代。 |
| [SAIGE](https://arxiv.org/abs/2609.19759)、[DyTopo](https://arxiv.org/abs/2602.06039) | 按需组织与 Need/Offer 引导的动态通信。 | 借鉴任务驱动关系；语义匹配本身不提供安全授权。 |
| [AgentPrune](https://arxiv.org/abs/2410.02506)、[AgentDropout](https://aclanthology.org/2025.acl-long.1170/) | 减少冗余通信或动态移除 Agent。 | 应作为成本压缩近邻；需证明风险事件提供了什么额外信息或触发价值。 |
| [SafeFlow](https://arxiv.org/abs/2607.25255) | 追踪恶意语义传播并检查工作流危险终点。 | 借鉴类型化传播证据；需验证冲突后可执行的替代组织及其成本。 |
| [DynaTrust](https://arxiv.org/abs/2603.15661)、[MeshHeal](https://arxiv.org/abs/2609.29015) | 动态信任、隔离、恢复或降级节点处理。 | 借鉴任务不中断的目标；需在相同攻击与任务条件下证明组织层增量。 |
| [Prudentia](https://arxiv.org/abs/2602.11416) | 将安全策略纳入 Agent 后续规划。 | 借鉴任务与策略一起考虑；需证明多实例交付和图结构选择的额外作用。 |

这些是研究定位，**不是**上述方法的官方复现或公平胜负结论。旧 README 的三图概念及历史文字可在 [基线提交的 README](https://github.com/ruihanzhu5-cyber/SecurityAdaptiveMAS/blob/16f90bca9092cdc2875265f79b86793c73b9bc48/README.md) 查阅；现行实现以单一生效图为准，原有 `docs/assets/` 文件未删除。

## 7. 实现状态与待解决问题

| 能力 | 当前状态 | 证据与限制 |
|---|---|---|
| 单一可执行图、网关、顺序 Worker | 已有并沿用 | [`graph_runtime.py`](security_adaptive_mas/graph_runtime.py)、[`dynamic_team.py`](security_adaptive_mas/dynamic_team.py)；不是并行加速。 |
| 任务／风险／成本进入同一候选表 | 本地实现，离线夹具验证 | [`controller.py`](security_adaptive_mas/controller.py)、[`joint_rewriter.py`](security_adaptive_mas/joint_rewriter.py)；风险是当前对象静态可达性，未来成本 `unknown`。 |
| 已知需求缺口的局部替代 | 狭窄实现，离线夹具验证 | 只复用已存在的独立结果；无可用对象时明确记录缺口。 |
| 按用途记录实际模型用量 | 本地实现，离线测试 | [`deepseek.py`](security_adaptive_mas/deepseek.py)；预算预检拒绝不算已消费。 |
| 独立合法任务完成判据、选择性交付、重新检索、时间展开风险 | 未实现／待定义 | 不能把官方 Runner 的 `success` 当作这些能力的证明。 |
| v0.3 真实攻击、防御与成本收益 | **未测量** | 没有 v0.3 付费 episode、成对轨迹或 placebo。 |

当前最重要的开放问题是：如何独立判断用户任务是否全部完成；怎样验证低可信内容中的必要字段而不把指令洗成授权；以及怎样从真实调用校准未来成本。未解决前，任何“更安全且更省 Token”的数值结论都没有依据。

## 8. 仓库导航与复现入口

| 文件 | 用途 |
|---|---|
| [`online_task.py`](security_adaptive_mas/online_task.py)、[`task_proposer.py`](security_adaptive_mas/task_proposer.py) | 保存已到达任务与提出只读执行契约。 |
| [`controller.py`](security_adaptive_mas/controller.py)、[`joint_rewriter.py`](security_adaptive_mas/joint_rewriter.py) | 触发、评估并选择有限局部补丁。 |
| [`graph_runtime.py`](security_adaptive_mas/graph_runtime.py)、[`graph_evaluation.py`](security_adaptive_mas/graph_evaluation.py) | 生效组织关系、需求覆盖与结构风险。 |
| [`dynamic_team.py`](security_adaptive_mas/dynamic_team.py)、[`are_bridge.py`](security_adaptive_mas/are_bridge.py) | 顺序 Worker 与官方 ARE 事件／工具接入。 |
| [`security.py`](security_adaptive_mas/security.py)、[`run_framework.py`](security_adaptive_mas/run_framework.py) | 授权网关、策略入口、预算和结果归档。 |
| [`docs/reports/`](docs/reports/)、[`docs/research-log/`](docs/research-log/) | 历史实跑证据与每日记录。 |
| [`data/gaia2/`](data/gaia2/README.md) | 五个固定场景、完整数据 Release、下载与场景选择说明。 |

零付费配置核验（需本机已配置仓库依赖和官方场景数据）：

```powershell
.\.venv\Scripts\python.exe -m security_adaptive_mas.run_framework --mode adaptive --scenario scenario_universe_22_kb5ah9 --organization-policy joint --attack single_injection --task-scope-policy sandbox_permissive --dry-run
```

真实模型调用另需进程环境变量 `DEEPSEEK_API_KEY`、明确的单次预算上限与 `--acknowledge-model-cost`。本轮只运行离线测试和 dry-run，没有发起新的付费请求。

## 9. 完整 Gaia2 数据

Git 中保留了五个历史开发场景的原始 JSON；[Gaia2 数据 Release](https://github.com/ruihanzhu5-cyber/SecurityAdaptiveMAS/releases/tag/gaia2-data-78ea3bd)保存固定上游版本的 7 个 Parquet 文件和 ARE 所需的文件系统快照。这样普通代码克隆仍然较小，需要扩展场景时再下载大文件。每个文件的 SHA-256 都写在 [`source_versions.json`](configs/source_versions.json)，下载接口会核对。

从完整数据选一个场景时，先运行 `python -m security_adaptive_mas.dataset_store fetch`，再用 `list --category execution` 查看 ID；`run_framework` 可用 `--dataset-category execution --scenario <ID> --dry-run` 检查入口。固定五例的运行和历史成绩不受此接口扩展影响。目录结构、命令和上游署名见[数据说明](data/gaia2/README.md)。
