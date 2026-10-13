# P1：任务增量分析与轻量性能触发接口

> 下文保留原 P1 的实施记录。当前 P1.1 修订以文末 [P1.1 Review Amendments](#p11-review-amendments) 为准，尤其是 root 来源保护、一次性工具关联和状态口径。

实施日期：2026-10-13。修改前本地 HEAD 为 `66989aa737ae0e49d6e9e2571613df38a25dc27b`，与实施指令的参考版本一致；工作区当时无未提交修改。接口版本标记为 `p1-shadow-interfaces-2026-10-13`，用于代码与接口审查。

本轮交付的是默认关闭的 Shadow 接口：已交付事件 → Python 预筛 → 显式请求任务提案 → 证据和结构校验 → 增量需求 → P/D/C/W 诊断及可解释判定。`RECONSIDER` 仅表示值得后续比较组织候选，不应用拓扑。**未做离线回放、未重新运行五例、未发起新的付费 DeepSeek 请求、未做攻击实验、未验证真实 TaskProposer API 行为，尚无自动 G_t 执行或性能改善结论。**

## 版本备注

- **版本标签**：`p1-shadow-interfaces-2026-10-13`；基于提交 `66989aa`。
- **交付范围**：增量需求维护、纯 Python 预筛、显式任务提案接口、共享预算与 Shadow 审计；新增两个生产模块。
- **默认行为**：Monitor 和任务 LLM 默认关闭；P1 的 RECONSIDER 只提供理由，不应用拓扑。
- **验证**：38 个离线测试通过，1 个上游 Pydantic 弃用警告；本次提交前核对代码与该测试版本一致。
- **保留边界**：自动 LLM 安全时点、官方 default 被动 hook、真实完成事实来源仍为 BLOCKED；真实 TaskProposer API 未验证。其余 OPEN 项见文末。
- **实验状态**：本版本未重新运行五例、未做离线回放或付费模型实验；已有五例 5/5 来自此前独立的 ARE default 单 Agent 基线，不能归因于 P1。

## 修改清单

| 文件 | 本轮职责与改动 |
|---|---|
| [online_observation.py](../security_adaptive_mas/online_observation.py) | 原事件字段之后追加身份、状态和计量；稳定事件 ID、重复交付检查、不可变快照、整份模型输入的计量接口 |
| [online_task.py](../security_adaptive_mas/online_task.py) | 沿用已有 TaskAnalysis/TaskUpdate；按 task_id 增量 upsert；依赖去重、移除和环校验；分开建议状态与事实状态 |
| [performance_monitor.py](../security_adaptive_mas/performance_monitor.py)（新增） | 纯 Python 预筛、诊断、分析去重及 RECONSIDER 原因；无模型、任务写入或执行权限 |
| [task_proposer.py](../security_adaptive_mas/task_proposer.py)（新增） | Prompt、可见材料组装、严格 JSON 解析和现有分析器校验；复用 DeepSeekClient |
| [controller.py](../security_adaptive_mas/controller.py) | 默认关闭的 P1 协调入口、待分析事件合并、共享预算检查、手动安全边界调用和审计日志 |
| [deepseek.py](../security_adaptive_mas/deepseek.py) | 可选 purpose 及同一 RunBudget 内的任务分析请求计数/上限；预算拒绝用 RuntimeError 子类 BudgetExceeded 表达 |
| [are_bridge.py](../security_adaptive_mas/are_bridge.py) | 已交付通知、成功工具结果和实际模型输入转交廉价预筛；回调内不调用任务提案器 |
| [test_performance_monitor.py](../tests/test_performance_monitor.py)（新增） | 8 个无付费模型调用的契约测试 |
| [test_online_planning.py](../tests/test_online_planning.py) | 扩展原有 Builder 测试，核验实际返回的通知进入 Shadow 待分析队列 |
| 本文 | 接口、计量边界、实测结果和 Unresolved Decisions |

本轮未修改 organization.py、dynamic_team.py、topology_candidates.py、are_adapter.py、trace.py、三个固定场景配置/划分文件，以及五例原始成功报告。没有新增 D_t/H_t 图服务。

## 对象、依赖与副作用

调用方创建 TraceLogger、episode 的唯一 RunBudget 和 DeepSeekClient；Controller 创建 Recorder、OnlineTaskAnalyzer 与 PerformanceMonitor。调用方显式注入 DeepSeekTaskProposer，并将 `controller.run_budget` 指向同一个 client.budget。Controller 不创建模型客户端或第二份预算。

```text
ARE 已交付通知 / 已成功返回的工具
  -> ObservedContext / GuardedARETool
  -> OnlineObservationRecorder.record_*() 返回事件
  -> Controller.on_observed_event() -> Python prefilter -> 待分析 ID + trace

调用方选定边界后，显式 process_pending_analysis()
  -> flags / 可用提案器 / 同一 episode budget / 请求上限检查
  -> DeepSeekTaskProposer -> DeepSeekClient.complete(purpose="task_analysis")
  -> OnlineTaskAnalyzer.apply_proposal() -> 增量需求
  -> PerformanceMonitor.signals() / reconsider() -> trace + 决策
```

Recorder 只记录事实，不导入 ARE、Controller 或 DeepSeek。TaskAnalyzer 只依赖在线观察。Monitor 只依赖在线观察与任务类型；它不改 TaskAnalysis，不调用模型或网关。只有 TaskProposer 调用真实 complete；Controller 负责调度和接收结果。新增 P1 入口不调用旧 `update()`、`safe_reconfigure()` 或候选执行路径。

已接通的 ARE 路径是已有受限 Bridge。它原本会替换工具集合，不能作为五例官方 default 基线的被动观察器。此次没有把它安装到原五例运行器。

## 事件接口与语义

原 `ObservedEvent(sequence, kind, evidence_id, text, tool_name)` 五个位置参数保持兼容。追加可选字段为 `event_id`、`agent_instance_id`、`call_id`、`status`、`simulation_time`、`elapsed_seconds`、`input_fingerprint`、`input_characters`。

```python
recorder.record_notification(kind, text, evidence_id, **metadata) -> ObservedEvent
recorder.record_action(tool_name, evidence_id, **metadata) -> ObservedEvent
recorder.record_tool_result(tool_name, evidence_id, **metadata) -> ObservedEvent
recorder.record_model_input(evidence_id, fingerprint, characters,
                           *, agent_instance_id) -> ObservedEvent
recorder.snapshot(topology_version=None) -> OnlineObservation
```

- 新事件默认 ID 为观察 run 内的 `event_<sequence>`，不是 ARE 原生消息 ID，也不保证跨重启一致。显式传同一 ID、同一事实时返回原事件；复用 ID 携带不同事实会报错。序列按新追加事件递增，旧快照保持不变。
- 通知只来自原 ARE `get_notifications()` 已返回的 USER/ENV，不读取 Scenario、Judge 或未来队列。`simulation_time` 取已交付消息的 `timestamp.timestamp()`，表示源消息的模拟时间戳，不宣称是新的精确交付时钟。
- 实例 ID 来自实际 ObservedContext，当前默认是 `agent_0`。没有为本轮创建 Worker 运行实例。
- 现有 GuardedARETool 接缝只在成功返回后记录动作和结果，状态写 `completed`。`elapsed_seconds` 是该次 Gateway/工具调用的墙钟耗时。没有观测到失败/拒绝的完整调用链，也没有真实 call_id，因此不计算精确重试，不补造 started/failed 事件。
- 工具原文仍由既有 ProvenanceStore 保存。Monitor 的新日志记录 ID、状态、理由和计量，不复制工具原文。

以下仅是契约示例，不是新实验记录：

```json
{"sequence": 2, "kind": "tool_result", "evidence_id": "ev_2",
 "event_id": "event_2", "agent_instance_id": "agent_0",
 "call_id": null, "status": "completed", "simulation_time": null,
 "elapsed_seconds": 0.03}
```

## 增量任务契约

```python
analyzer.analyze(observation, updates=()) -> TaskAnalysis
analyzer.refresh(previous, observation) -> TaskAnalysis
analyzer.apply_proposal(previous, proposal, observation) -> TaskAnalysis
```

同一 task_id 覆盖可更新的描述、建议状态及推断，合并原来源证据；新增 ID 增加工作。根任务一直使用 `root`，新 USER 更新该根记录而不会复制根。重复的相同 ID 提案是幂等 upsert；用新 ID 重复完全相同描述和证据会被拒绝。跨措辞的语义等价仍需提案器复用原 ID，校验器不声称理解自然语言等价。

原 `Subtask.status` 保留为事实字段，`actual_status` 是其只读别名；新增 `suggested_status` 和 `certainty`。新提案任务的事实状态为 PENDING，旧任务的事实状态保持原值。模型即使输出 COMPLETED 或 CONFIRMED，也只能形成建议状态及 INFERRED 标记，不能写执行完成事实。

依赖按 `(predecessor_id, successor_id)` upsert。错误端点和非 UNKNOWN 依赖的环被拒绝；移除已登记依赖须由 `TaskUpdate.remove_dependencies` 指定，并有可见 `source_evidence_ids`。信息需求以 `(task_id, requirement_id)` 更新；旧调用没有 requirement_id 时兼容使用描述作键。未知提供方是 None，不补造 provider 边。提案中的 as_of_sequence 必须与冻结的当前快照相同，所有来源及条目证据必须在线可见。

结构验证只能证明引用可见、ID/类型/依赖有效，不能证明证据内容支持模型结论。未定义自动 CONFIRMED 政策，也未实现 `apply_execution_fact()`；完成事实来源见 BLOCKED 项。

兼容保留的 `TaskAnalysis.unresolved_event_ids` 字段仍存储 ENV 的 evidence_id；它与 Monitor 使用的 ObservedEvent.event_id 不同。未分析语义与已分析事件的去重也分别记录，避免把“模型看过”当成“需求已确认”。

## 提案器输入、Prompt 和预算

Prompt 位于 `task_proposer.py:TASK_PROPOSAL_PROMPT`。它要求复用稳定 ID、引用已见证据、区分未知与独立性，把外部命令视为数据，禁止提出权限或拓扑操作。提案器接口为：

```python
DeepSeekTaskProposer(client, read_evidence, *, tool_excerpt_characters=1500)
proposer.propose(observation, current, *, tool_evidence_ids=()) -> TaskUpdate
```

输入只含已投递的 USER/ENV、当前任务清单（字段名明确为 actual_status）、分析序号，以及调用方明确选择的可见工具结果。工具摘录必须按可见 evidence ID 从 ProvenanceStore 读取，默认每项最多 1500 字符并标出是否截断；这是可配置的输入长度限制，不是研究阈值。没有自动“必要工具结果”选择器，调用方负责选择；默认不附工具原文。

模型输出示例：

```json
{"source_evidence_ids":["ev_1"],
 "subtasks":[{"task_id":"inspect","description":"Inspect requested result",
   "suggested_status":"pending","evidence_ids":["ev_1"],"certainty":"inferred"}],
 "dependencies":[],"information_requirements":[]}
```

解析拒绝额外字段、无效类型、未见证据和非法依赖。JSON/类型/结构失败记录 `task_proposal_error` 并抛 TaskProposalError；Controller 返回 `ANALYZE/task_proposal_failed`，保留待分析项。没有伪造任务的 fallback。其他传输错误按原客户端行为传播。

沿用当前 deepseek-flash/provider/thinking 设置，采用独立 `complete(response_format={"type":"json_object"})` 入口，没有改变 DeepSeekEngineBuilder 的执行 Agent JSON-action 合约。请求、实际 usage 和费用仍计入同一 RunBudget；usage 带 `purpose="task_analysis"`。默认最多 3 次任务分析 API 尝试，重试同样计数。上限不是性能阈值。RunBudget 原有 UTF-8 字节预检只用于预算估算，不是实际 token 测量；实际 usage 来自模型响应。本轮只用注入 completion 测试，没有验证真实 API 的 JSON 格式兼容性。

## Controller 与触发接口

```python
Controller(..., monitor_enabled=False, task_llm_enabled=False,
           task_proposer=None, max_analysis_requests=3,
           runtime_agent_instances=None)
controller.on_observed_event(event, *, related_requirements=()) -> TriggerDecision
controller.process_pending_analysis(*, tool_evidence_ids=()) -> TriggerDecision
controller.performance_snapshot() -> PerformanceSignals
```

默认关闭时不会创建请求或新决策日志。仅打开 monitor 时，USER/ENV 得到 ANALYZE 并合并到待分析队列，普通工具结果得到 KEEP；手动 process 返回 `analysis_deferred_disabled`。只有 task_llm 显式开启、提案器可用且共享同一 RunBudget 时才可能发出请求。预算上限或预算预检拒绝返回 `analysis_deferred_budget`，事件继续待分析，不被视为无关。成功处理后按稳定事件 ID 标记已分析；重复提交不再触发提案。

工具结果与已登记需求的关联须由调用方通过 `related_requirements=((task_id, requirement_id),)` 明确提供。它只请求 ANALYZE，不确认需求已满足。已验证提案改变工作描述、依赖或信息需求结构时返回 RECONSIDER；无结构变化时返回 KEEP 并保留 `insufficient_evidence` 理由。没有工作独立性判断、候选净收益分数或 spawn 指令。

所有新增 P1 入口只更新观察/需求/诊断状态。TopologySpec、Gateway、授权阶段和 Agent 数量不由这些入口修改；旧安全检查不读取性能请求上限。P1 没有 topology_apply 入口，等价于始终关闭。自动 process 调度没有接通。

## P/D/C/W 的实测边界

| 指标 | 当前输出和单位 | 数据来源及限制 |
|---|---|---|
| P | value=None，unknown，fraction_of_total_work | 没有可靠时长或完整已知依赖，不计算 critical_path/total_work；ready_work_count 同样 unknown，单位 work_items |
| D | InformationRequirement 元组 | 保留具体任务、需求 ID、可选 provider_task_id、证据与确定性；不压成分数 |
| C | 有输入观测时为 observed，单位 repeated_request_characters；无观测时 unknown | ObservedContext 实际提交的整份输入序列化文本的哈希和字符数；只计完全相同请求的再次提交。0 只表示已有观测中未发现整份重复，不代表无局部重复 |
| W | 已明确声明单运行实例时 not_applicable；否则 unknown，agent_coordination_seconds | 不使用 ARE 到时限的环境等待，不把工具墙钟耗时或 RunBudget 模型延迟当作团队协调浪费 |

C 不支持逐段 evidence token 归因，也不声称能测节省 token。ProvenanceStore 的可见来源父集合不能证明每段已原样出现在模型输入；因此只采用整份实际输入计量。任务提案器的 token 会计入共享 usage，但不通过执行 Agent 的 ObservedContext 生成 C 计量。

## 最小测试结果

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:LITELLM_LOCAL_MODEL_COST_MAP='True'
$env:HF_HUB_OFFLINE='1'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests
```

最终实测：**38 passed，1 warning**（Pydantic 的上游 class-based config 弃用警告）。新增 8 个测试覆盖 Recorder 兼容与不可变快照、USER/ENV/普通结果预筛、按 ID 更新与事实状态保留、证据/环/依赖移除、UNKNOWN/NA 和 C 单位、注入提案解析与共享 usage、显式错误、Shadow 无改图及含重试的请求上限。原 Builder 测试另验证已返回通知进入待分析队列。所有模型响应来自 completion_fn 注入，没有付费模型请求，没有执行场景或轨迹回放。

还核对了新增模块的单向 import 依赖、Git diff 空白检查及固定配置/原基线报告未进入修改清单。真实工具失败计量、运行时完成事实及无副作用的官方 default 接入没有被这些测试验证。

## Unresolved Decisions

| 状态 | 源码位置 / 影响 | 最小选项、建议与阻塞范围 |
|---|---|---|
| **BLOCKED** | `are_bridge.py:observed_get_notifications`；上游 `are/simulation/agents/default_agent/are_simulation_main.py:agent_loop` 在 get_notifications 后进入 react_agent.run。额外同步 LLM 可能改变模拟时序 | A：保持显式手动边界，真实在线时序不做等价声明；B：核验官方 pause/resume 和计时政策后增加 hook。建议先 A；阻塞自动 process，不阻塞本轮接口 |
| **BLOCKED** | `are_bridge.py:make_guarded_agent_builder/guarded_init_tools` 会替换工具集合；不能据此观察官方 default 五例基线 | A：继续把受限 Bridge 与原基线分开；B：下一轮实现并验证不改工具、Prompt、参数、权限或时间政策的被动 hook。建议先 A；阻塞“原基线已接入 Monitor”的声明 |
| **BLOCKED** | `online_task.py:apply_proposal`、`online_observation.py:WorkState`、`are_bridge.py:GuardedARETool.__call__` 没有可靠任务生命周期映射 | A：以后由执行器提供经过核验的 task_id/完成事件；B：人工可信完成事实入口。建议先确定 A 的证据语义；本轮不实现完成事实写入，也不使用 Judge/摘要判定 |
| **OPEN** | `online_task.py:Certainty/apply_proposal` 校验的是证据可见性，不能证明文本支持语义 | A：限定任务的规则复核；B：人工确认协议。定义前全部模型结论保持 INFERRED/UNKNOWN，不阻塞 Shadow |
| **OPEN** | `performance_monitor.py:prefilter` 的 related_requirements 及 `task_proposer.py:tool_evidence_ids` 由调用方提供 | A：执行器登记工具结果与信息需求的映射；B：模型提出关联后另行核验。建议先 A；当前关联只请求分析，不写满足事实，未自动选取摘录 |
| **OPEN** | `performance_monitor.py:signals` 缺时长、ready 事实和协作等待窗口 | A：未来采集任务执行时间/可信消息交接；B：保持 UNKNOWN/NA。建议本轮 B；阻塞数值 P/W 和性能阈值，不阻塞结构变化的 RECONSIDER |
| **OPEN** | `online_task.py:InformationRequirement.key`、`apply_proposal`；缺稳定 ID 的旧信息需求用描述作键，跨措辞等价无法自动证明 | A：新提案始终使用稳定 requirement_id（当前协议）；B：增加语义等价判定。建议 A；任务/信息需求撤销政策仍未定义，当前只支持 upsert 和显式依赖移除 |
| **OPEN** | 上游 `notification_system.py:Message` 没有原生消息 ID；Recorder 的观察 ID 只能对同一次已记录交付去重 | A：调用方保存并重复传入原观察 event_id；B：以后核验上游事件身份接缝。建议 A；不能仅按消息文本认定两次交付是同一事件 |
| **OPEN** | `task_proposer.py:propose` 的 response_format 和 excerpt 长度 | A：审查后明确同意一次真实接口验证；B：继续只用注入响应。当前 B；阻塞真实 API 兼容性结论，不阻塞解析接口 |

## 接入真实运行与 P2 的最小依赖

先审查以上安全时点、被动 hook 和完成事实语义，再明确同意真实任务分析请求与预算；随后才能验证在线事件顺序、默认 Agent 行为等价及真实 JSON 提案。P2 还需要实际组织执行接缝、可信任务收益/通信成本估计、已知依赖与运行实例身份。当前 TaskAnalysis 与 RECONSIDER 理由可作为只读输入，但候选比较和应用不在本轮。

## P1.1 Review Amendments

修订日期：2026-10-13。审查基线与修改前本地 HEAD 均为 `b4bfbf5e2c34ce4dab22b8bf3df36671e8344888`；工作区当时干净。本轮仅修订已确认的接口契约，版本标记为 `p1.1-shadow-interfaces-2026-10-13`，供下一轮 Review。原 P1 的 38 项测试记录是历史记录，本轮测试结果见 C 节。

### A. 修改摘要

| 文件 | 实际改动 | 必要性 |
|---|---|---|
| [online_task.py](../security_adaptive_mas/online_task.py) | 普通 TaskUpdate 一律不能包含 root 子任务；refresh 每次从可见 USER 描述及 USER 证据同步 root，保留已有实际状态 | 证据可见不等于有权改写用户目标；ENV/TOOL/LLM 提案不得覆盖根目标 |
| [task_proposer.py](../security_adaptive_mas/task_proposer.py) | Prompt 明确禁止在 subtasks 输出 root；JSON 继续经过分析器硬性校验 | Prompt 只提供指引，实际拒绝规则由 Python 执行；未改 JSON Schema 或预算 |
| [performance_monitor.py](../security_adaptive_mas/performance_monitor.py) | 未关联工具结果使用 tool_result_unlinked；复用一个小型关联键校验方法，核对类型、明确 requirement_id 和当前登记项 | KEEP 只表示没有已验证的重分析触发；合法关联才允许 ANALYZE；无 ID 的旧需求不能用描述冒充稳定 ID |
| [are_bridge.py](../security_adaptive_mas/are_bridge.py) | ObservedContext 增加显式一次性绑定/消费；GuardedARETool 在调用前消费，成功结果透传关联键 | 保留受信任串行调度代码的可选接缝；失败时不记录成功结果，也不把关联泄漏到下次调用 |
| [controller.py](../security_adaptive_mas/controller.py) | 提供当前需求校验入口；既有日志追加事件 ID、待分析、分析成功及结构变化字段 | 明确区分 observed、pending、proposed/unverified；不增加冗余原文日志或自动 LLM 调度 |
| [test_performance_monitor.py](../tests/test_performance_monitor.py) | 修订旧 root upsert 测试；扩展注入提案拒绝；新增工具一次性绑定及空提案状态两个回归 | 验证本次真实接口边界；不将响应注入视为 Gaia2 或 DeepSeek 实验成绩 |
| 本文 | 追加当前修订、测试与待决台账，保留原 P1 历史 | 避免用旧接口描述覆盖当前行为或改写旧实验结果 |

没有新增生产模块，没有修改 online_observation 的事件模型。

### B. 接口调用路径

```text
真实已交付 USER -> Recorder -> analyze / refresh(root)
                              root 描述与证据只取 USER；原实际状态保留

已交付 ENV / 成功 TOOL -> 已有受限 Bridge -> on_observed_event -> Python prefilter
                                                               |
可信串行调度调用方 --显式绑定一次--> ObservedContext --消费绑定-----+
  （当前没有官方 default 自动调用方）                              |
                                                         Pending Analysis
                                                               |
                          调用方手动选择边界 -> TaskProposer -> Python 校验
                                                               |
                                      root 提案拒绝；非 root 保持 INFERRED/UNKNOWN
                                                               |
                                      KEEP / ANALYZE / RECONSIDER；不 apply G_t
```

**已接通：**受限 Bridge 已交付通知、成功工具结果进入 Python 观察回调；可选关联由 GuardedARETool 成功结果传给 Controller。**仅提供接口、未自动接入：**真实调度方如何知道该次调用关联哪个需求、官方 default 如何提供 task/requirement/call 上下文，以及何时自动处理待分析。没有在现有运行器中自动调用绑定方法或 process_pending_analysis。

根任务规则：`apply_proposal()` 在普通子任务 upsert 之前拒绝任何 `task_id="root"`，包括原描述不变的提案，报 `root_requires_delivered_user_notification`。`refresh()` 采用 Recorder 当前可见的 USER 汇总描述和 USER evidence_ids，同步同一个 root；不将 ENV 证据保留为根目标来源。实际 status 仍由原记录保留，模型不能写入完成事实。

一次性关联接口：

```python
context.bind_next_tool_requirements(((task_id, requirement_id),)) -> None
context.consume_tool_requirements() -> tuple[tuple[str, str], ...]
controller.validate_related_requirements(keys) -> tuple[InformationRequirement, ...]
```

- 仅供受信任的串行执行调度代码调用，未注册为 ARE 工具，也不解析邮件或模型文本来设置关联。当前没有自动绑定调用方。
- 绑定保存当前已登记需求的不可变记录；键必须是非空字符串组成的二元 tuple，引用明确的 requirement_id。绑定时与消费时均核对当前需求。记录在两次检查间发生变化则报 `stale_information_requirement_binding`，不存在或格式非法也明确报错。
- 新绑定替换待消费绑定；设置非法绑定会先清空旧绑定并报错。调用前消费意味着原工具/Gateway 失败、参数拒绝或绑定过期都不会把它带到下一次调用。原执行异常继续传播，没有新增 catch/fallback；失败不产生 completed tool_result。
- 默认空绑定不做需求映射。Monitor 开启时普通结果为 `KEEP/tool_result_unlinked`，不宣称语义无关；合法关联为 `ANALYZE/registered_information_result`。两者都不会确认需求满足或写任务完成。
- 关联只表明值得分析。后续手动处理若需要工具原文，调用方仍须通过 `process_pending_analysis(tool_evidence_ids=(... ,))` 选择对应可见摘录；没有自动相关性判断器。

状态与 ID 口径：

| 记录 | 当前精确含义 |
|---|---|
| ObservedEvent.event_id | 本次实际观察的事件身份；不冒充上游 call_id |
| pending_analysis | 按 event_id 保存被预筛选出、尚未成功完成提案流程的事件 |
| analyzed_event_ids | 按 event_id 标记合法提案流程已成功；不表示内容可信或工作已完成 |
| TaskAnalysis.unresolved_event_ids | 兼容保留 ENV evidence_id；表示尚未被任务条目证据引用，不是“未完成任务”或“模型没看过”的同义词 |
| actual_status / suggested_status | 原实际状态与模型建议分开；本轮没有可信执行完成写入接口 |
| RECONSIDER | P1 仅比较需求结构变化，表示值得以后比较候选；signals 参数未参与真实成本判断 |

合法结构提案的三个数组全部为空时，仍可记录 analysis_succeeded=true、proposal_status=proposed_unverified、requirements_changed=false，并移出待分析队列；未被任务条目引用的 ENV 证据仍可留在 unresolved_event_ids。KEEP 的理由仍含 insufficient_evidence，不升级成“不相关”。裸 JSON 数组 `[]` 不符合现有提案 Schema，仍明确拒绝。

既有 trace 追加少量字段：prefilter_decision 包含 event_id、pending_analysis、related_requirements；trigger_decision 包含 event_ids、analysis_succeeded、requirements_changed。未运行分析或因预算延迟时 analysis_succeeded=None；提案失败时 False；合法提案流程成功时 True。performance_signals 继续保留各指标的 UNKNOWN/NA，不复制工具原文。

P/D/C/W 算法本轮未变：P 与 ready 数缺事实时 UNKNOWN；D 保留关系与未知提供方；C 只有整份实际输入的重复字符代理量；W 明确单实例时 NA，否则 UNKNOWN。**这不是 P/D/C/W 联合优化或净收益决策。**

### C. 最小测试结果

实际命令：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:LITELLM_LOCAL_MODEL_COST_MAP='True'
$env:HF_HUB_OFFLINE='1'
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests
git diff --check
```

实测 **40 passed，1 warning，29.16 秒**；唯一警告是原有上游 Pydantic class-based config 弃用警告。测试输出保存在本机 `work/test_logs/performance_monitor_phase1_1_2026-10-13.txt`。

本轮修订原有根任务测试，验证任意 root 提案拒绝、原实际状态保持、新 USER 刷新且不复制根；扩展注入 JSON 提案测试验证 Python 硬拒绝 root。新增两个测试分别核对：显式合法关联、无绑定原因、错误/过期键拒绝、消费一次、工具失败不产生成功事实且不泄漏绑定；以及空提案的 pending/analyzed/unverified 状态与 ID 区分。已有 Shadow 断言继续核对 Spec、消息/工具网关及授权阶段保持原值。

验证只使用针对性工具函数与 completion_fn 响应注入，没有真实场景运行、离线轨迹回放、攻击评测或付费模型请求；未验证真实 TaskProposer API、官方 default 被动 hook 或并发调度。

### D. OPEN / BLOCKED 台账

| 状态 | 位置 → 缺失事实/接口 | 最小 A / B 选项 → 本轮影响 |
|---|---|---|
| **OPEN** | `are_bridge.py:bind_next_tool_requirements`；官方 default 无可靠 task_id/requirement_id 调度调用方和真实 call_id | A：受信任串行调度代码显式提供已登记键（本轮仅接口）；B：核验官方执行上下文后再接入。阻塞自动关联，不阻塞可选契约；不能用工具名或文本猜配 |
| **OPEN** | `GuardedARETool.__call__` 的既有 Gateway 调用固定 agent_0；共享 Context 的并发、可重入及执行中需求变动未验证 | A：仅在当前串行 coordinator 上下文使用可选接口；B：先核验真实调用/实例身份，再设计明确按调用关联。当前只测 A；未改变旧执行身份或网关权限，多实例/并发接入仍阻塞 |
| **BLOCKED** | `controller.py:process_pending_analysis` / 上游 agent_loop；额外模型调用的无副作用时点不明确 | A：继续手动调用；B：先核验官方暂停/恢复与计时政策。当前 A；不自动在工具回调调用模型 |
| **BLOCKED** | `are_bridge.py:make_guarded_agent_builder` 替换工具集合，与官方 default 五例能力不同 | A：保留分离；B：独立验证被动 hook。当前 A；不能宣称 P1.1 已接入原 5/5 基线 |
| **BLOCKED** | `online_task.py:apply_proposal`；没有证明需求已满足、工作已完成的可信运行记录 | A：未来执行器提供可核验的任务完成事件；B：明确人工可信事实入口。当前均未实现；关联/模型建议不提升完成状态 |
| **OPEN** | `online_task.py:refresh`；新 USER 改变目标后，旧 root 生命周期事实如何对应新目标版本尚未定义 | A：以后定义根目标版本与执行事实绑定；B：保留历史实际状态，等待可信执行器更新（当前）。不阻塞来源保护，但不能把旧 COMPLETED 当作新目标也已完成；未自动撤销或重开状态 |
| **OPEN** | `online_task.py:apply_proposal` / task_proposer；证据可见不等于支持语义，外部内容何时可形成新子任务未有完整确认政策 | A：限定规则复核；B：人工确认。当前非 root 仍 INFERRED/UNKNOWN，网关授权不随提案变化；不新增 LLM Judge |
| **OPEN** | `performance_monitor.py:signals/reconsider`；缺时长、独立性、协作等待窗口及真实候选成本 | A：未来采集可信信号；B：保留 UNKNOWN/NA 与结构变化建议（当前）。阻塞性能收益结论，不阻塞本轮诊断接口 |

### E. 守住的边界

默认 monitor_enabled=False、task_llm_enabled=False 保持不变。没有新增生产模块、事件字段、预算或自动调度器；没有修改 DeepSeek 模型、API Base、temperature/thinking、ARE 上游、Gateway 授权、旧组织选择模块或实际拓扑。可选绑定无调用方时为空；没有伪造 Worker、call_id、需求满足或任务完成事实。

五个 Case、manifest、数据划分/哈希和此前 5/5 报告未改，未重跑。原成绩来自此前官方 default 单 Agent 开发基线，与本轮 P1.1 无因果关系。代码及文档随 `p1.1-shadow-interfaces-2026-10-13` 版本提交供 Review；本轮没有进入 P2。
