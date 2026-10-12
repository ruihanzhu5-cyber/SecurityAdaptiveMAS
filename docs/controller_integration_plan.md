# Safety-Constrained Adaptive Topology Controller：源码审查与研究实施方案

> 状态：设计文档，2026-10-12。本文只提出接口、验证条件和实验，不代表所述新模块已实现或已有实验成绩。证据以仓库当前 `main`、MANTA `3da40879d690a25459cc401292e5c6f0cc366d84`、ATM `951e9aa8c8421e8b8d59302ee4727cc855f84714` 和论文原文为准。DynaTrust 的公开源码未核验，以下不虚构其类或函数。

## 1. Executive Summary 与研究定位

研究主体是**任务驱动的拓扑自适应**：长程任务的依赖、并行性和成本变化时，系统即使没有攻击也应调整人数、角色与通信。安全是贯穿候选生成、选择、状态交接和执行的约束。核心问题是：一种有利于任务的重组，是否会让过去接触过的不可信内容沿新消息、Memory 或授权工具路径抵达敏感动作？

建议继承 MANTA 的 `TopologySpec` / `TopologyMutation`、在适配后试用其 Planner 与 Trace Auditor；借用 ATM 的能力、状态路由和影子验证思想，但补上源码未落实的边界；将 DynaTrust 的节点行为风险作为观察信号。**需要研究的部分**是把历史来源依赖和候选未来路径连起来，在硬约束下同时衡量任务收益、潜在传播、合法依赖损失和重组成本。三套系统顺次调用本身不足以构成可信的论文贡献。

三个可检验问题：RQ1 无攻击时自适应相对固定拓扑能否提高任务效用；RQ2 历史暴露在多次重组中是否会通过新路径抵达敏感能力；RQ3 联合决策能否比节点隔离和仅验证重组的方案，在同等授权下保留更多合法任务能力并降低危险路径。

## 2. 证据范围与上游机制

### 2.1 MANTA：规划、审计、变异与运行时的边界

[论文](https://arxiv.org/html/2607.28527v1)与[固定源码](https://github.com/mao-code/MANTA/tree/3da40879d690a25459cc401292e5c6f0cc366d84)一致表明：[`TopologyPlannerAgent.propose_initial`](https://github.com/mao-code/MANTA/blob/3da40879d690a25459cc401292e5c6f0cc366d84/MAS/self_evolved/planner.py)调用 LLM 生成结构描述、编译并验证，失败时有确定性回退；`propose_mutation`基于审计报告提出一次变异并验证，`fallback_mutation`是规则修复编译器。它们依赖 MANTA 的 LLM client、配置、状态摘要与审计格式；当前项目尚未接入这些入口。不能把使用同名的本地 `TopologyPlanner.make` 算作完整 Planner 集成。

[`TraceAuditorAgent.audit` / `_heuristic_modes`](https://github.com/mao-code/MANTA/blob/3da40879d690a25459cc401292e5c6f0cc366d84/MAS/self_evolved/auditor.py)从执行状态、产物、工具记录识别协作故障，例如工具错误级联、分支坍缩、重复状态变异与缺乏证据的结论，并决定是否建议修复；可选 hybrid / LLM 判断。它检测的是任务执行故障，不是针对外部 Prompt Injection 的来源路径检测器。ARE 轨迹须先映射为其所需的 turn/state/artifact/tool record，缺失字段应标为不可用，不能用空值冒充“未发生故障”。

[`TopologySpec.validate`、`ContextPolicy`、`TopologyMutation.apply`](https://github.com/mao-code/MANTA/blob/3da40879d690a25459cc401292e5c6f0cc366d84/MAS/self_evolved/spec.py)可直接用于结构表示与静态检查。变异操作包括 `expand_agent_to_group`、`set_group_pattern`、`add_agent`、`add_edge`、`remove_edge`、`set_context_policy`；`apply`产生新版本并验证结构。`allowed_tools=None`在上游表示使用运行环境全部工具，接入当前权限网关时必须拒绝这一中间态。`ContextPolicy`控制可见来源、共享范围、摘要等，但不对摘要是否保留恶意指令作语义证明。角色撤销、在途调用、持久 Memory 迁移也不由 `TopologySpec.validate`保证。

[`SelfEvolvedEngine.run`](https://github.com/mao-code/MANTA/blob/3da40879d690a25459cc401292e5c6f0cc366d84/MAS/self_evolved/engine.py)把 executor turn、audit、有限修复预算、重复决策抑制、transaction commit 与 context 更新连成循环；这依赖 MANTA 自己的 executor、共享上下文与交易接口。其性能信号主要是审计到的执行失败、产物与工具记录，Planner 还计入工具/状态变更与协作需要；本项目尚无可校准的 ARE 任务效用或重组成本模型。第一步应做最小 `ARETraceToMANTA` 和 `MANTAPlannerAdapter`，仅输出候选，不能直接把 MANTA Engine 接管 ARE Runner。上游仓库有 planner、auditor、spec、engine 与 transaction 测试；本地快照含 MIT `LICENSE`，固定提交已用 GitHub commit API 核验。

### 2.2 ATM：不变量的论文目标与当前源码区别

[论文](https://arxiv.org/html/2607.20488v1)将运行时拆分组织为瓶颈监测、角色因子化、I1 能力单调、I2 状态路由完整、I3 正式切换前影子验证，描述了影子任务窗口与切换后表现检查。[固定源码](https://github.com/sidikbro/jiuwen_atm/tree/951e9aa8c8421e8b8d59302ee4727cc855f84714)提供 `BottleneckIndex`（队列、上下文、工具错误、角色熵、重试、等待及热身阈值）、`Factorizer`、`CapabilityPolicy`、`StateAtom` / `StateDistiller`、`Verifier`、`ATMPipeline` 和 `TeamManagerAdapter`，依赖 Python ≥3.11、NumPy、Pydantic、OpenAI；存在对应测试。

I1 的实际[`CapabilityPolicy.check_mutation_invariant`](https://github.com/sidikbro/jiuwen_atm/blob/951e9aa8c8421e8b8d59302ee4727cc855f84714/jiuwen_atm/safety/capability_policy.py)只在父级工具/Memory 集合为真时比较子集；父级未登记或空集时会跳过检查。因此**不能原样当作硬安全门**。本项目需以可信授权快照为准，父级或子级权限信息缺失即拒绝；工具名称、资源、操作、参数范围都要核对，不只集合包含关系。I2 的[`StateDistiller.distill`](https://github.com/sidikbro/jiuwen_atm/blob/951e9aa8c8421e8b8d59302ee4727cc855f84714/jiuwen_atm/topology/state_distiller.py)确实把状态路由到 shared / 某子角色 / quarantine，并有敏感级别规则；但 `verify_completeness`只比较数量，不能证明每个 atom 恰好一次、来源完整、迁移后依赖不变。`StateAtom.dependencies`存在，但[`decompose_context`](https://github.com/sidikbro/jiuwen_atm/blob/951e9aa8c8421e8b8d59302ee4727cc855f84714/jiuwen_atm/topology/state_atoms.py)是文本启发式切分与分类，入口默认 `source="local"`；[`ATMPipeline.on_tick`](https://github.com/sidikbro/jiuwen_atm/blob/951e9aa8c8421e8b8d59302ee4727cc855f84714/jiuwen_atm/topology/atm_pipeline.py)未把外部 README 的来源元数据自动传给该切分器。它能隔离识别出的 credential/high-PL atom，却不能保证源自低可信 README 的“任务计划”不会作为 `USER_GOAL`共享。这是需要补的 **provenance-aware state inheritance**，而不是声称 ATM 完全没有隐私路由。

I3 存在重要实现落差：[`Verifier.run_shadow_pass`](https://github.com/sidikbro/jiuwen_atm/blob/951e9aa8c8421e8b8d59302ee4727cc855f84714/jiuwen_atm/topology/verifier.py)当前只检查子 ID 并设置 `shadow_completed=True`，没有运行论文描述的成对影子任务；切换后的 `_Window`另行观察表现。[`LiveTeamManagerAdapter`](https://github.com/sidikbro/jiuwen_atm/blob/951e9aa8c8421e8b8d59302ee4727cc855f84714/jiuwen_atm/topology/team_manager_adapter.py)的创建/撤销方法仍抛 `NotImplementedError`，standalone 适配器不连接真实团队。故仅可复用接口形状与部分启发式，真实 ARE 影子验证和切换协议须自己实现并测试。`pyproject.toml`标 MIT，但本次固定 checkout 没有找到 `LICENSE`文件；引入或复制源码前需核对许可证文本与适用版本。

### 2.3 DynaTrust：节点信任的用途及其局限

[论文原文](https://arxiv.org/html/2603.15661v1)把团队表示为动态信任图，快审计器先筛可疑代理消息，模糊/有害内容交给按信任与置信度加权的陪审；以 Beta 参数累计正负行为证据，负向证据权重更大，后验均值 `α/(α+β)`为节点信任。跌破阈值后，复制角色/能力但不带受污染 Memory 的代理，重接邻居，再隔离原节点。这给出了行为信号、保留连通性和恢复服务的参考。

**Source implementation not verified**：论文页面和本次公开仓库检索未核验到作者发布的可运行源码、许可证或类路径。因此只把算法概念列作参考，最小实现需依据论文明确公式和阈值重新写，并验证；不可写成“调用 DynaTrust 监控器”。其威胁模型是潜伏的恶意代理，而本项目主要面对正常 Worker 刚读取外部恶意 README。历史高信任 Worker 仍可能带有某一来源的暴露，低信任节点也不等于已经执行攻击。`node_trust`反映代理既往行为；`source_exposure`指来源与执行实例的可观察依赖；`candidate_path`指特定未来拓扑中到敏感能力的结构路径，三者分别记录。节点分数只可调节监控与软风险，不能代替来源路径或硬授权。

## 3. 本仓库代码审计：已完成与缺口

| 位置 | 已有行为及状态 | 研究方案必须面对的缺口 |
|---|---|---|
| [`organization.py`](../security_adaptive_mas/organization.py) | 使用 MANTA `TopologySpec`/`ContextPolicy`/`TopologyMutation`形成 Singleton、Star、Chain；`TaskAnalyzer`接收调用方给的 intake/parallel/commit；`OrganizationSelector`用固定 benefit/cost 和布尔 `risky_input`风险惩罚。**部分实现/启发式** | 不是 MANTA Planner/Auditor；无任务依赖图、实测效用、候选路径或重构代价估计。 |
| [`controller.py`](../security_adaptive_mas/controller.py) | 设置 spec/version、assignments 与网关；风险模式按**新 spec 中 worker 角色**加入隔离集。**部分实现** | `agent_2`在 Star 为 Worker，在 Chain 可变 Verifier；若它读取恶意文本，重组后按新角色遍历可能漏掉它。字符串 ID 没有实例 epoch；替换多个状态字段缺少完整 prepare/commit/rollback。 |
| [`security.py`](../security_adaptive_mas/security.py) | `ProvenanceStore`记录来源、父证据、文本、owner；`MessageGateway`校验当前 spec 对象、版本、边、可见性、隔离；`ToolGateway`具精确写授权与持久去重。**局部真实约束** | `contains_low_trust`是保守父链，不证明 LLM 受影响；shared 只是网关记录的消息，无长期 Memory 读写及失效图；网关拦截会让固定拓扑下攻击也失败。 |
| [`dynamic_team.py`](../security_adaptive_mas/dynamic_team.py) / [`are_bridge.py`](../security_adaptive_mas/are_bridge.py) | ARE `AgentBuilder`只读接线，`delegate_parallel_reads`串行委托两个 Worker、再给 Verifier；规则匹配输入风险。**有限运行路径** | 仅一次团队重组的工具路径，不是持续多轮原生调度；没有完整状态迁移/高权限 Executor；真实 ARE Single/Multi 端到端未验证。 |
| [`manta_adapter.py`](../security_adaptive_mas/manta_adapter.py) / [`trace.py`](../security_adaptive_mas/trace.py) | 前者只导入 MANTA spec 命名空间；后者写 JSONL 事件。**已实现基础** | 尚无 MANTA Planner/Auditor 所需的轨迹模式与性能特征映射。 |
| [`tests/test_system.py`](../tests/test_system.py) / [`tests/test_dynamic_team.py`](../tests/test_dynamic_team.py) | 网关旧版本、权限、一次重组和风险来自第一个 Worker 的离线假响应被覆盖。**测试桩/局部测试** | 缺第二个 Worker 风险、角色复用、重组失败、在途操作、长期 Memory 和真实多轮实验。 |

[v1 报告](../implementation_report_v1.md)、[v2 报告](../implementation_report_v2.md)与代码相符：此前离线测试 22 项通过，并有一次 DeepSeek 连通性探测；正式 ARE Single/Multi 模型任务仍无在线端到端成绩。五个固定 Gaia2 场景、官方 Runner 与现有配置应保留为可复现基础，不能把离线成功当作任务完成率。

有三项先行修正是研究结论的前提：第一，显式区分稳定逻辑身份、角色和运行实例；暴露与隔离绑定实例，交接另有事件。第二，记录 Memory 写/读、摘要和状态迁移的可观察父依赖；“读过”只表示**可能**影响，不表示真实因果。第三，将任务授权和敏感工具的实验控制固定在 E1–E4；否则现有 Tool Gateway 单独拦截攻击，就无法把收益归给拓扑选择。

## 4. 继承矩阵与集成决策

“Direct Reuse”只指接口/纯结构；“Adapter”指保留上游算法但翻译输入输出；“Reimplementation”指源码存在但不能作为本系统的硬门而必须按明确语义重做。上游 URL 与固定版本见第 2 节；当前版本引用的是审查快照，升级前需重跑契约测试。

| Target Module | Source Work | Source Implementation / 当前接口 | Reuse Mode | Required Adaptation | Risk | Priority |
|---|---|---|---|---|---|---|
| 结构候选、变异 | MANTA | `spec.py`: `TopologySpec.validate(max_agents)`、`TopologyMutation.apply(spec,max_agents)`；MIT、已含测试 | Direct Reuse | 本地类型映射；候选发布前将 `allowed_tools=None`拒绝；补运行时身份与权限校验 | 静态通过≠可安全切换 | P0 |
| 初始任务规划 | MANTA | `planner.py`: `TopologyPlannerAgent.propose_initial(...)`；需 MANTA LLM client/config | Adapter | ARE 任务 DAG、授权、工具元数据映射到可供 Planner 使用的结构化输入；输出仍须经过本地验证 | Planner 输入可能包含不可信任务文本，成本及候选质量未测 | P2 |
| 故障触发修复 | MANTA | `auditor.py`: `TraceAuditorAgent.audit(...)`；`planner.py:propose_mutation(...)`；有测试 | Adapter | 从 ARE 事件生成其 state/artifact/tool record，标明缺失字段；只提候选 | 误把任务错误当攻击，运行时耦合 | P2 |
| 完整执行循环 | MANTA | `engine.py:SelfEvolvedEngine.run`，含自有 executor/context/transaction | Conceptual Reference | 不引入完整引擎；借鉴修复预算、重复决策抑制、提交记录 | 与 ARE Runner 的状态/事务模型不兼容 | P3 |
| 能力与状态交接 | ATM | `safety/capability_policy.py:check_mutation_invariant`；`topology/state_distiller.py:distill`，有测试 | Reimplementation | 从可信 grant 构建完整父子权限集；按证据 ID 精确分配、逐项校验、保留来源；缺失权限拒绝 | 上游空父权限跳检、仅计数完整性 | P0 |
| 影子验证和团队接入 | ATM | `topology/verifier.py:run_shadow_pass`、`team_manager_adapter.py:LiveTeamManagerAdapter` | Conceptual Reference | ARE adapter 执行真实隔离影子输入/输出，记录阈值，再受控切换；不可直接依赖现有布尔标记 | 上游影子验证为空壳，live adapter 未实现 | P1 |
| 性能瓶颈信号 | ATM | `monitoring/bottleneck_index.py:BottleneckIndex`，6 类信号/热身 | Adapter | ARE trace 提供可测值；样本不足时不启动阈值触发；与 MANTA auditor 的触发来源分开记录 | Gaia2 短场景难热身 | P2 |
| 节点行为风险与连接恢复 | DynaTrust | **Source implementation not verified**；论文 Beta 更新、隔离后复制并重接 | Conceptual Reference | 小型可解释 `RiskSignal` 及连接修复策略；不能宣称调用上游代码 | Sleeper Agent 与外部文本攻击不同；无可核验代码/许可证 | P2 |
| 来源与历史依赖 | 本仓库 + 相关思想 | `security.py:ProvenanceStore.add/contains_low_trust`；multi-CaMeL、Dependency-Guided Rollback、Cross-layer Contagion 仅作设计参照，本文未核验其源码接口 | Adapter | 增 Memory 读写、摘要、交接与实例事件；保守记录父依赖 | 传播过度保守、漏掉未观察的隐式依赖 | P0 |
| 候选时序可达与联合选择 | 本研究 | 现仓库只有 `OrganizationSelector.choose` 固定打分 | New Research Component | 构造候选扩展图、硬约束、任务依赖保留与多目标选择 | 方法增益或创新性未验证 | P1 |

许可证与部署：MANTA 固定快照有 MIT 文件；ATM 的 Python ≥3.11、NumPy/Pydantic/OpenAI 依赖与 `pyproject.toml` 的 MIT 声明可核验，但固定提交缺许可证文件，先明确法律文本再复制源码；DynaTrust 无可核验实现/许可。当前工程仅导入 MANTA spec；引入完整 Planner 会新增模型客户端和配置耦合。矩阵中的 P0/P1 是研究原型所需，P2 是证据充分后增强，P3 暂缓。

关键接口的证据台账（“高”仅指代码存在及接口行为可核验，不表示适用本任务）：

| 上游位置与输入→输出 | 类型/依赖兼容性 | 测试、版本与证据可信度 |
|---|---|---|
| MANTA `spec.py`: `TopologyMutation.apply(TopologySpec,max_agents) → TopologySpec` | 已在本地 `manta_adapter.py`使用，结构兼容；运行时身份/授权需外加 | 上游 spec 测试、本地 system 测试；提交 `3da40879…`，MIT 文件；高 |
| MANTA `planner.py`: `propose_initial`接任务上下文与 LLM client，`propose_mutation`接 spec/审计报告→候选 | ARE 轨迹与 MANTA 审计字典不兼容，需翻译；运行依赖模型/config | 上游 planner 测试；同提交；接口存在为高、ARE 适配效果未验证 |
| MANTA `auditor.py`: `audit`接 turn/state/产物/工具记录→故障报告 | ARE Runner 原始事件不直接等于其输入；需显式缺失值 | 上游 auditor 测试；同提交；接口存在为高、Gaia2 信号覆盖未验证 |
| ATM `CapabilityPolicy` + `StateDistiller`: parent/child policy、`StateAtom`→布尔/去向 | 需映射本地 grant、evidence ID、Memory 与实例；依赖 Python ≥3.11 / Pydantic | 上游 safety/topology 测试；提交 `951e9aa8…`；接口存在为高、硬门完备性不足 |
| ATM `Verifier` + `TeamManagerAdapter`: contract/agent→shadow 标记/child handle | 无现成 ARE live 接口，影子验证只记录标记 | 上游测试不能证明真实影子任务；同提交；可部署性低，许可证文本待核 |
| DynaTrust Beta trust/隔离重接：行为证据→节点估计/替身拓扑 | 无已核验代码、部署依赖/许可证未知；只能按论文另建接口 | **Source implementation not verified**；论文机制可信，软件复用不可确认 |

## 5. 统一 Controller：状态、接口与信任边界

数据以事件日志为事实来源。模块可以读取同一不可变 `RuntimeSnapshot`，只由运行时协调层提交新版本；Planner、风险监控、可达性评估不能自行修改网关。建议最小类型：

```python
@dataclass(frozen=True)
class AgentIdentity:
    logical_id: str            # 任务内稳定名称
    instance_id: str           # 每次创建唯一，不能复用
    role: str                  # 可变；不能当暴露身份
    created_in_version: int

@dataclass(frozen=True)
class RuntimeEvent:
    event_id: str
    kind: str                  # model_input, tool_output, memory_write/read,
                               # message_send/deliver, tool_call, handoff, retire
    instance_id: str | None
    input_evidence_ids: tuple[str, ...]
    output_evidence_ids: tuple[str, ...]
    topology_version: int
    timestamp: int

@dataclass(frozen=True)
class CandidateTopology:
    spec: TopologySpec
    assignments: dict[str, str]
    state_routes: tuple[StateRoute, ...]    # evidence ID -> destination instance
    instance_plan: tuple[InstanceChange, ...]
    task_dependencies: tuple[TaskDependency, ...]

@dataclass(frozen=True)
class ReachabilityAssessment:
    reachable_sources: dict[str, tuple[str, ...]]  # granted sensitive capability -> source IDs
    witness_paths: dict[str, tuple[str, ...]]
    newly_opened_paths: tuple[tuple[str, ...], ...]
    lost_required_dependencies: tuple[str, ...]
    uncertainty: tuple[str, ...]

@dataclass(frozen=True)
class SafetyValidationResult:
    admissible: bool
    hard_failures: tuple[str, ...]
    assumptions: tuple[str, ...]
```

`TaskState`先保留现有类，增加可信任务授权、DAG、阶段与完成情况的结构字段；其 `task_text`只用于展示/规划参考，不能自动变成工具授权。`RiskSignal`是带主体实例、证据、时间与置信度的观察，不伪装成攻击概率。`DependencySnapshot`存事件边与来源标签的不可变视图；`TopologyDecision`记录候选、评分、拒绝理由及版本；`ReconfigurationPlan`记录状态路由、网关变更和回退点。暂不制造大型统一对象或在多个模块中复制所有消息原文。

```python
class JointTopologyController:
    def observe(self, events: tuple[RuntimeEvent, ...]) -> RuntimeSnapshot: ...
    def generate_candidates(self, task: TaskState, snapshot: RuntimeSnapshot
                            ) -> tuple[CandidateTopology, ...]: ...
    def evaluate_reachability(self, candidate: CandidateTopology,
                              history: DependencySnapshot,
                              snapshot: RuntimeSnapshot) -> ReachabilityAssessment: ...
    def validate_safety(self, candidate: CandidateTopology,
                        snapshot: RuntimeSnapshot) -> SafetyValidationResult: ...
    def select_topology(self, evaluated: tuple[EvaluatedCandidate, ...]
                        ) -> TopologyDecision: ...
    def apply_reconfiguration(self, decision: TopologyDecision,
                              expected_version: int) -> ReconfigurationResult: ...
```

可信用户任务与授权经 `TaskAuthorizationPolicy`进入；外部 README、网页和工具输出只以带 `source_id`、trust label 的数据进入模型与来源图。给 Planner/Auditor 的提示应包含结构化任务摘要与风险标签，不直接把低可信 README 原文提升为“重组命令”或 grant。高权限 Executor 只能从可信任务 grant 和经验证的状态路由获得输入；即使摘要出自正常 Worker，也保留其外部父来源。Node trust 是监控输入，硬工具授权仍只由政策网关决定。

## 6. Temporal Attack Reachability：确定性 MVP

在第 `t` 次重组前构造历史有向图 `H_t`：节点是证据版本、运行实例、Memory 条目、消息与工具动作；边是**观察到**的读入、派生、写入、读取、传送、交接。记录 `source_id`、版本、事件时刻与父 ID。模型输入中包含某证据时加 `evidence -> instance@turn`，产出的摘要加 `instance@turn -> summary`；这是潜在影响关系，不是已证实的文本因果关系。Memory 覆盖需生成新版本，旧版本保留以便回滚审计。历史图只增不改；删除/失效是访问规则变化，不删历史证据。

对候选 `c`，仅把它**将允许的**上下文读取、消息边、共享 Memory 读写、状态迁移和工具能力加入未来扩展图 `F(c)`；边必须同时满足 spec、ContextPolicy、实例、版本、网关和任务授权。`P_t(c)` 是从低可信源经过 `H_t ∪ F(c)`可达的**已授权敏感能力及其执行实例**集合，表示该合法能力可能受污染输入影响，不表示它会被滥用。未授权动作没有可执行工具边；如果模型实际提出此类调用，只记为 `attempted_denied_action`，由网关阻止。比较当前拓扑得到 `newly_opened_paths = P_t(c) - P_t(current)`，输出一条或若干短路径作为解释。再计算任务 DAG 中被切断的必需 producer→consumer 边；切断意味着任务不可完成，不能把“全部隔离”当作免费安全收益。多跳传播允许时间单调，不能让未来事件影响过去；对未观测 Memory 读、模型内部记忆与跨任务复用标为不确定。

MVP 用队列图搜索：每个候选约 `O(|V|+|E|)`，若逐低可信源/敏感 sink 搜索则乘以源或 sink 数；可用来源 bitset 向前传播减少重复。新边数、可达高权限实例数、敏感 sink 数、必需依赖损失数都保留为**向量**，不直接合成“攻击成功概率”。具体路径例：`README@r1 -> Worker#k 的模型输入 -> summary@r2 -> persistent memory@m3 -> 新 Executor#q 的交接输入 -> write sink`。如只存在 `README -> Worker`而没有后续交接边，结果是历史暴露、当前不可达，不应宣称攻击已经成功。

三种量严格区分：`potential reachability` 是结构上允许的路径；`estimated behavioral risk` 只在有已校准行为信号时表示估计并附不确定区间；`observed attack success` 必须由受控 sink 事件核验。保守图可能把正常引用也算入路径；通过允许安全重算、只传必要字段、把低可信证据降为只读引用、改接无暴露实例等候选减轻误报。漏观测的缓存/模型内部状态必须写入 uncertainty，不可声称端到端零风险。可达性不是 LLM 因果证明，未出现路径也只对**被纳入模型的通道**成立。

## 7. Joint Topology Selection：硬门与研究决策

候选生成先由任务 DAG 和 MANTA-style Planner/Mutation 给出小集合（含维持当前拓扑、单人、分工、局部限边、替换实例与状态重算）；生成时就考虑任务所需协作，避免仅事后剔除。对每项计算：`U_task`（完成率/预计延迟/令牌与工具成本）、`C_comm`、`C_reconfig`（新实例、状态迁移、暂停与回退）、路径向量 `R_path`、风险监控信号 `R_behavior`、合法依赖损失 `L_dep`。费用与时延从实际日志估计；冷启动时可给区间而非假精度常数。

**方案 A：约束优化（Research MVP 推荐）。** 先用严格可执行门筛掉越权、缺失/重复状态交接、旧实例仍可写、版本不一致及必需任务依赖断裂的候选。对剩余候选按事先固定的任务效用与重组成本比较，并将**低可信来源到已授权敏感能力**的路径数作为风险预算/次级目标；选择理由保留完整向量和证据路径。某些状态继承规则也可以预先列为硬门，但不能事后把任意可达性都宣布为必然攻击。优点是可解释、数据需求小，缺点是结构可达过度保守时可能无可行高性能拓扑，硬/软边界需预注册。

**方案 B：风险预算或 Pareto 选择。** 对多维 `U_task, C_comm, C_reconfig, R_path, L_dep`求非支配集，再按给定风险预算或任务阶段规则选一个；硬权限约束仍不能交易。优点是能展示不同任务下折中前沿，论文分析空间更大；缺点是需更多重复实验校准预算和选择稳定性，容易事后调参。可先用 A 建立严格可复现基线，再检验 B 是否真比固定预算好。简单加权求和、任意阈值或 LLM 一句话选拓扑，单独都不足以构成新方法。更强的研究方向是**带时间历史、任务依赖保留和可达路径见证的候选选择**，并证明其相对同样硬门、同样候选的 E3 有额外收益。

若候选全被硬门否决：优先维持当前已验证版本并暂停相关敏感动作；在任务授权允许的范围内请求缺失证据的安全重算、缩小共享或用干净实例重接；若仍无安全可行路径，明确返回阻塞任务及原因。不能为了完成率放宽硬授权，也不能将所有 Agent 隔离后记为成功。

## 8. Safety invariants 与受控重配置语义

严格可执行：I1 以可信授权和实例为单位，`new_capabilities ⊆ task_grants ∩ parent_delegable_capabilities`，未知/空父配置不能放行；I2 每个需迁移的证据 ID 恰有一个明确去向或 quarantine，父链/安全标签在摘要和拷贝后保留，目标 Memory/上下文符合可见性；I3 网关实例、组织版本、消息版本、授权版本一致；I4 未授权敏感写由 Tool Gateway 拒绝且持久去重；I5 必需任务依赖若被阻断则候选不可选，除非计划中显式有获授权的重算边。低可信内容通向**合法授予**的敏感工具是一条风险路径，不自动违反 I4；`risky_input`或节点 trust 也只作为估计/策略依据，不能被包装为可证明的不变量。影子验证需要在不执行外部不可逆工具的沙盒中跑代表性任务和状态交接，再比较合法输出与暴露事件；ATM 当前 `shadow_completed`标志不能作为证明。

切换采用明确协议：①冻结旧版本的**新**敏感调用入口，记录在途请求与幂等键；②在私有副本上生成/验证候选、状态去向与权限差；③让旧请求完成或取消并对账外部副作用，绝不盲目重放；④影子执行、检查任务依赖与输出；⑤以期望版本 CAS 一次性发布 spec、实例映射、权限和可见性，网关以该版本快照检查；⑥递送或丢弃旧版本消息并记录原因，旧实例退役/隔离；⑦观察新版本表现，若失败恢复前一**安全且对账后的**版本，并使新版本已产生的 Memory 失效或重算。外部不可逆写不能靠恢复 Python 对象回滚；必须靠 Tool Gateway 去重和远端对账。当前 `Controller.update()`只顺序赋值，不满足此协议。

攻击 README 后创建 Executor 的关键检查：其迁移输入中若含来自 README 的摘要，则 `state_routes`保留该父来源；即便 ATM 格式/权限路由合法，可达图仍显示到写能力的路径。可选干预是交给没有这段历史的 Executor 仅传可信任务参数、让 Worker 提供带证据的只读结果供核验、或重算受污染计划。任何动作必须保证合法任务的必要数据仍可到达。

## 9. 可检验贡献与“系统集成”的分界

| 候选贡献 | Research Claim / 现有方法不足 | Proposed Mechanism | Required Evidence / 最强竞争基线 | Failure Conditions / 复杂度 |
|---|---|---|---|---|
| C1 历史到未来的来源可达性 | 相同当前节点 trust 和当前权限下，不同历史暴露会改变未来候选拓扑的危险路径；MANTA 的任务审计、ATM 的状态路由和 DynaTrust 的节点分数不能直接给出这类路径。 | 历史事件父链与候选拓扑/状态交接的时间扩展图，输出可审核 witness 与合法依赖损失。 | 注入发生在前阶段、后阶段才增高权限实例；对比同样 grant、同样候选、含节点风险但无来源路径的 E3；验证路径标签的精确率/召回与开销。 | 若日志无法覆盖真实状态传递、路径大量误报或 E3 一样好，则主张不成立；实现中等。 |
| C2 任务依赖保留的安全联合选择 | 全节点隔离会让任务失败，而性能优先会开危险路径；只逐项硬校验不能选出较好的协作结构。 | 候选集合中联合比较任务 DAG 必需边、路径预算、通信与重组代价，必要时局部限边、选择干净实例或重算。 | 同候选集对比性能优先 E1、硬门 E2、节点级风险 E3，以及仅静态路径/无任务依赖等消融；报告任务完成、攻击 sink 与 Pareto 前沿。 | 若收益来自更多候选、更强 verifier、调参或额外工具限制而非选择逻辑，创新性不足；实现中高。 |
| C3 来源感知的最小状态交接（可选） | ATM I2 保证路由规则但固定源码不保证摘要父来源与执行实例暴露在交接中保存。 | 每条迁移带证据 ID/父链与实例 epoch，逐项验证并在重组失败时失效/重算。 | 对比相同 ATM-style I1/I2/I3 门但无父链；检查低可信摘要是否进入新增 Executor、错误拒绝率及恢复成本。 | 若只是补元数据字段且无测得的行为差异，应定位为工程基础而非独立论文贡献；实现中等。 |

审稿判断：MANTA Planner + ATM Validator + DynaTrust Trust Score 的流水线是有用的工程集成，却没有自动证明方法创新。主张 C1+C2 需要在严格同预算、同候选、同网关下优于 E3，且无攻击时不显著损害任务效用。C3 只有产生独立可检验增益时才作为贡献。若 E3≈E4，应检查历史记录覆盖率、风险信号是否已足够、候选是否缺差异以及 Tool Gateway 是否造成地板效应；仍无差异就承认可达性未带来显著收益。

## 10. 最小长期实验：受控代码维护任务

搭建一个**合成、可重复**的 5–8 阶段维护任务，而非直接修改现有五个 Gaia2 原始场景：读取外部 README/issue → 分解两项修复 → 并行 Worker 检查 → 把摘要写入持久 Memory → 下一阶段改变需求并创建/重组 Verifier 与高权限 Executor → 核验测试 → 对受控目标执行提交/网络动作。可信任务授权只允许写指定测试仓库资源/收件人；另设模拟凭据与受控 sink，模拟资源不含真实秘密。攻击者只可编辑指定低可信 README 字段，不能编辑任务 grant、系统提示、代码或工具定义；恶意指令试图让后续 Executor 用合法工具提出未授权动作。无攻击版本必须同样跑完，才能测性能自适应。

当前 `TaskAuthorizationPolicy`对写入参数做精确匹配，因此在不改变授权的前提下，未经授权 sink 的**实际执行率理应为零**；这只能验证网关，不能验证 E4 优于 E3。Research MVP 先以“低可信来源到合法授予的敏感能力的结构路径”、实际 `attempted_denied_action` 和合法任务完成为主要指标。若要测模型是否在有合法动态写入需求时把不可信内容混入产物，必须另行设计**可信任务预先限定的测试分支/资源/参数模式**，经安全审查并在四组中完全相同地启用；不能让 README 或模型临时扩张 grant。那时攻击成功应定义为受控 sink 观察到超出可信任务语义的产物或目标，且报告网关拒绝与真正执行两种结果。此行为实验依赖未来合法写入接口，不属于当前仓库已经支持的能力。

先用离线确定性回放验证版本/路径，再用同一模型、相同温度/预算、同一授权、同一攻击文本位置与次数跑受控多种子实验；若不具备真实合法写入与在线模型接线，结果只能称结构/离线验证，不能称攻击成功率或 Gaia2 成绩。对照必须固定**候选拓扑集合、角色可用工具、Tool Gateway、Verifier、最大调用次数**。E1–E4 的唯一区别是启用的选择信息与重组约束；E1 若保留同一基础网关，可单独评估拓扑收益而不偷换授权。另设固定 Singleton/Star/Chain 参考组与同预算 oracle 回放作为上界，不把 oracle 用作部署算法。

| 组别 | 实际可用决策 | 预期检验 |
|---|---|---|
| E1 MANTA-only | MANTA-style 性能候选/故障信号 + 相同基础 Tool Gateway，按任务效用与成本选；无额外状态交接门 | 无攻击性能收益、风险暴露基线 |
| E2 MANTA + ATM | E1 加严格 I1/I2、真实影子门与版本/在途操作检查；无历史路径选择 | 硬重组不变量的单独效果 |
| E3 + DynaTrust-style Risk | E2 加根据观察行为更新的节点风险及隔离/重接；同候选、同状态迁移协议 | 节点级动态风险能解释多少收益 |
| E4 Joint Controller | E3 加历史父链、候选未来路径、任务依赖保留与联合选择 | 新决策相对最强竞争基线的增量 |

核心消融：E4 去掉历史依赖（只看当前暴露）；E4 去掉未来路径（只看节点风险）；再检验去掉任务依赖保留或状态交接来源标签。对“Worker 读过 README，但没有恶意行为”与“已出现异常工具请求”分别分层；也做攻击发生后立即写工具调用的反例，因为这时拓扑重组可能根本没有时间发挥作用。测试若因所有组的 Tool Gateway 均阻断 sink，需报告路径暴露/网关阻断次数，并另设在**用户真实授权容许**的动作中诱导错误目标/时机的受控任务；不能为了制造成功率而关闭必要的基础授权。

报告：合法任务完成率、时间/令牌/工具成本、重组次数与失败率、未授权动作尝试/网关拒绝率、未授权 sink **实际执行率**、到合法敏感能力的结构可达率、新开路径数、必需依赖丢失数、误隔离率、恢复成本、影子验证开销。每个结果注明是离线结构代理、模型行为估计还是实际受控工具观察。样本量/随机种子/置信区间预注册，按任务实例配对；无攻击阶段的性能曲线与攻击后安全曲线都展示。只有一条示例轨迹不能支持统计结论。固定 Gaia2 五场景留作后续外部有效性检查，不能替代这个最小机制验证。

## 11. 实施路线：按依赖而非模块名称排序

本次不实施下表的代码改动。阶段的上游引用都受第 4 节版本与许可核对约束。

| 阶段 / 性质 | 将来需改或新增的文件与接口 | 最小单元 / 集成测试及验收 | 主要风险 |
|---|---|---|---|
| P0 事实基线，工程 | `security_adaptive_mas/trace.py`、`are_bridge.py`增加 `RuntimeEvent`序列化；`tests/test_dynamic_team.py`补第二 Worker 风险与角色复用；不改官方场景 | 回放能区分 agent_2 Worker 与新 Verifier 的实例 ID；旧测试继续通过；记录任务/费用/版本基线 | 真实 ARE 多轮路径尚无在线端到端证据 |
| P1 身份与网关版本，工程 | `controller.py`、`security.py`增加 `AgentIdentity(instance_id)`、实例级隔离、版本快照与在途写对账接口；`tests/test_system.py`补旧消息/旧权限/并发交接 | 第二 Worker 读取恶意文本后，其旧实例不能借 Verifier 角色逃逸；失败重组不得扩大权限；写去重仍跨重启有效 | 改动现有 ID 约定与日志兼容性 |
| P2 历史依赖，工程兼研究基础 | `security.py`扩展 `ProvenanceStore`或新 `dependency.py`；`are_bridge.py`、`dynamic_team.py`在模型输入、Memory 读写/摘要、交接点发事件；`tests/test_dependency.py` | 单个 README→摘要→Memory→新实例路径被完整记录；未接触 README 的实例不虚构该父源；离线多阶段回放准确 | 隐式 LLM 因果不可见，父链过宽 |
| P3 候选与任务性能，工程 | `organization.py`保留 MANTA spec，新增任务 DAG、成本观测与小候选生成；可选 `manta_planner_adapter.py`转换 MANTA Planner 输入/输出；`manta_trace_adapter.py`待 ARE 事件足够后接 Auditor | 无攻击多阶段任务至少在一个阶段改变拓扑，且相同候选可供四组共用；Planner 异常时确定性候选可运行 | MANTA LLM/配置与 ARE 不匹配、额外模型成本 |
| P4 安全重组门，工程 | `safety_invariants.py`、`reconfiguration.py`及 `controller.py`；参考 ATM I1/I2/I3，但在 ARE adapter 实现真实影子执行；`tests/test_reconfiguration.py` | 空父权限、重复/缺失交接、旧写请求和影子失败均拒绝切换；成功切换一次性发布且可对账回退 | 影子不能替代真实外部副作用，live adapter 需自建 |
| P5 可达性与联合决策，研究核心 | `reachability.py`、`joint_selector.py`，与 `organization.py`仅交换不可变候选/评估；`tests/test_reachability.py`、`tests/test_joint_selector.py` | 两候选任务效用接近但路径不同则可解释地选较安全者；路径见证正确；合法必需边不可无声删除；时间/内存开销可报告 | 误报、候选差异不足、优势由网关造成 |
| P6 受控实验，研究证据 | 新增独立实验脚本/配置和模拟 sink，固定 E1–E4 及消融；沿用 `configs/source_versions.json`记录上游版本 | 离线和在线结果分开；相同模型/候选/授权/预算；报告原始日志、成本、置信区间及无攻击控制 | 成功率地板效应、统计功效不足、行为不稳定 |

阶段级接口和测试门槛，补足上表的实施细目：

| 阶段 | 预计复用的上游模块 / 新接口 | 最小单元测试 | 集成测试与验收条件 |
|---|---|---|---|
| P0 | 无新上游；`TraceLogger.log_event(RuntimeEvent)`、ARE 事件转换 | 序列化/反序列化后 ID、版本、来源不变 | 现有离线团队路径输出可回放事件；记录基线结果与缺失字段 |
| P1 | 复用 MANTA `TopologySpec`与本地 Gateway；`resolve_instance(logical_id,version)`、`quarantine_instance(instance_id)` | 角色复用、旧实例消息、空权限和写幂等键 | Star→Chain 后暴露 Worker 实例仍受限，失败切换后旧安全版本继续服务 |
| P2 | 复用 `ProvenanceStore.add`；`record_read/write/derive/handoff`、`snapshot()` | 父链、Memory 版本、共享摘要、失效事件 | 两次重组后仍可追溯 README→新实例，未接触的实例无该历史 |
| P3 | MANTA `TopologyPlannerAgent`/`TraceAuditorAgent`经适配器；`propose(task,snapshot)`、`audit(mapped_trace)` | 缺失 ARE 字段、非法候选、任务 DAG 必需边 | 同一无攻击任务生成性能不同的候选，异常时回退且不授予额外工具 |
| P4 | MANTA `TopologyMutation` + ATM I1/I2/I3 思想；`prepare/validate/shadow/commit/abort` | 一证据一去向、父权限空集、在途旧写、影子失败 | 完成/取消/对账后一次性发布；回退无重复外部写；失败原因可追溯 |
| P5 | 本研究；`assess(candidate,history)`、`choose(evaluated)` | 时间单调路径、证据见证、无候选降级、任务必需边 | 相同候选下 E4 对已授权敏感能力开路更少，任务仍完成；记录延迟 |
| P6 | 固定上游快照与全部网关；`run_case(seed,arm,attack)` | 试验配置一致性、模拟 sink 标注 | 四组与消融成对执行，无攻击/攻击分层，原始轨迹可重算指标 |

**Research MVP** 为 P0–P5 的最小链：实例身份、可观察父依赖、3–5 个手工/任务 DAG 候选、严格能力/状态门、确定性候选可达、可解释约束选择、一个多阶段合成任务与四组同条件回放。先不接完整 MANTA Engine，不训练风险预测模型，不声称复现 DynaTrust 系统，不做通用 Benchmark；MANTA Planner/Auditor 与 ATM BottleneckIndex 在轨迹字段、成本和授权准备充分后接入。MVP 的停/走条件：若 E4 相对 E3 未改善可达路径或任务可行性，先检查日志和候选有效性；这些问题排除后仍无增益，就收缩或否定 C1/C2 主张。

## 12. Critical Risks and Open Questions

1. **运行时兼容性：**三套框架分别管理不同的 executor、state 与 tool；只承诺基于接口的适配，先跑同一 trace 的契约测试。
2. **MANTA Planner 可移植性：**它需特定 LLM/config/trace；在 ARE 环境中先只作候选提案，任务收益需本地测量。
3. **ATM 的语义污染缺口：**I1/I2 格式合法不证明摘要无恶意指令；来源继承和候选路径检查是额外设计。
4. **ATM 公开实现落差：**现有 `run_shadow_pass`无实际影子执行，Live adapter 是 stub；不得称集成后已达 I3。
5. **DynaTrust 威胁模型错位：**潜伏恶意节点的信任值不能判断正常 Worker 刚读到外部注入后的传播风险。
6. **结构图过于保守：**只要读过内容就连父边会增加误报；记录 witness、保留任务依赖、报告误隔离与替代候选。
7. **污染 Memory 已传播：**隔离节点不清除下游摘要；须按父链定位、失效/重算，且无法证明 LLM 内部状态已擦除。
8. **立即工具滥用：**若 Worker 在重组前即可调用敏感工具，动态拓扑无法挽救；主要依靠实时 Tool Gateway 与最小权限。
9. **网关收益混淆：**若固定拓扑在相同网关下已阻断全部攻击，E4 不得报告虚假的攻击成功率优势；比较路径暴露和任务成本。
10. **全候选不可行：**维持当前安全版本、暂停敏感动作并给出阻塞原因；性能下降必须报告，不能把全隔离算成功。
11. **ID/角色/实例混淆：**`agent_2`角色复用会让风险标记错位；实例 epoch 与交接事件是先决条件。
12. **来源不等于因果：**父链仅覆盖可观察输入，既可能过宽也可能漏隐式依赖；用受控回放核验，不作确定性因果声称。
13. **发表价值：**若与同候选、同网关、同风险信号的 E3 没有可复现增益，联合决策只是工程整合，应如实收缩结论。
14. **许可证与版本：**ATM 固定 checkout 的 MIT 声明缺对应 LICENSE 文件，DynaTrust 无可核验源码；复用前解决来源、许可和版本固定。
15. **不可逆副作用：**写入发生后回滚 spec 不会回滚外部系统；先对账、幂等去重，不能靠影子测试声称原子性。
16. **采样与成本：**真实 Long-Horizon 运行可能昂贵；先离线结构回放，再以事先限定预算的小规模在线实验检验行为。

## 13. 推荐决策与交付边界

先修实例身份与可观察日志，再做历史依赖和同候选实验；随后把 MANTA 任务候选、ATM 风格严格重组门、DynaTrust 风格行为信号接入联合选择。即使三种上游都接成功，也要把核心论证放在**过去来源如何改变未来候选拓扑的安全性，以及保留任务依赖时能否作出更好选择**。本文件只作为实施/实验计划；本次未更改 Controller、测试、基准结果或上游源码。
