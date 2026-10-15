# v0.4 本地实施与验收报告

日期：2026-10-14（Asia/Shanghai）。状态：代码实施与离线验收完成，等待 Review；本次保存代码快照，不代表研究验收完成或已验证效果。

## 1. 基础版本和权限

开始时分支为 `v0.3-joint-controller`，HEAD 为 `274f92ce086c4fcaa6554fc0455ea44e2fcdde64`，与任务书一致，工作区干净。执行了 status、branch/HEAD 核对和最近五条提交检查。代码实施阶段没有 reset、stash、commit、push、PR 或 merge。

后续用户于 2026-10-14 明确要求“推送github先，保存commit”，因此将本轮代码、配置、README 和本报告保存为 Git 提交并推送到同名远程分支。提交备注记录离线测试结果及仍然存在的 OPEN / BLOCKED；真实实验和研究效果仍待验证。

本机 ARE 是源码归档目录，没有单独的 `.git`；因此没有用其父仓库的 HEAD 冒充 ARE 版本。额外读取了官方固定提交的 Git tree，按 Git blob 算法核对 `are/` 内 258 个 Python 文件，全部与 `a9fd2edccb4bc7e22fbf45c8482a5fef30a2a5a4` 一致，无缺失或修改。核对输出保存在本机 `work/v0_4_are_source_verification.json`。

本轮遵循文档 02 的代码范围，文档 01 解释设计。只有代码、配置、必要文档和有限非付费验收。本轮真实 DeepSeek API 请求数为 **0**。测试使用明确的离线响应夹具；真实 ARE 工具验收只读取隔离场景，不调用模型服务。

## 2. 实际改了什么

以前的完整执行路径主要围绕“一个或两个通用 Worker，把结果给 Coordinator”。新配置进入下面这条主链；不带新配置时继续走已有入口，历史五例和实验记录未改写。

```text
run_framework --design-config
  → 校验研究配置、数据划分、能力模式和总预算
  → ARE 的实际到达通知进入在线观察
  → 同一个 DeepSeek 客户端提出工作任务、角色、AND/OR 需求和补做建议
  → Controller 校验来源、保护用户目标、登记 WorkingPlan
  → FrameworkTeam.dispatch_roles
  → joint_rewriter.generate_rewrite_plans / select_rewrite_plan
  → GraphRuntime.preview / apply
  → 有界 activation 调度与按边交付
  → 角色实际模型调用 / GuardedARETool / 同一工具网关
  → 真实对象、产物检查、输出槽绑定
  → 风险变化时重新分析；依赖满足后才允许继续敏感动作
```

### 2.1 任务、角色与计划

用户目标由已到达的 USER 消息生成 GoalContract。模型只能提工作任务、撤回推断出的前置条件和修改 WorkingPlan；不能改 root 或把外部材料升级为授权（`online_task.py`、`task_proposer.py`、`controller.py`）。

角色现在包含执行步骤、实际输入对象、申请工具、输出字段、产物检查和生命周期。实例另有角色版本、工具能力和实际历史。换职责不会清除暴露记录；需要新实例时实际创建，初始化来源也会留在计划中（`organization_contracts.py`、`graph_runtime.py`）。

模型响应按明确 schema 解析，非法字段、不可用工具、不存在的输入和未到达证据会报错并保存响应。v0.4 不把这些错误吞掉后返回 KEEP。

新版本各方法共用已有的、经核验的内存沙箱应用工具上限，排除 Files 和附件传输。严格策略仍要求已实现的可信操作范围；选择 sandbox_permissive 才使用相应的沙箱授权语义。有限写委派还需可信配置列出具体工具，模型不能自行增加。

### 2.2 结构、协议与真实数据路由

`topology_candidates.py` 增加了 13 个生成族。Star/树使用明确双向关系，Chain 使用有向顺序关系，fork–join/diamond 分支汇合，clustered 分组连接，small-world 使用局部邻接和固定种子捷径，hub-biased 使用按度偏好的连接。Delaunay 使用小规模空外接圆枚举算法，不依赖缺失的 scipy；共线、共圆等不适用坐标明确拒绝。

`dependency_schedule`、`one_round_exchange`、`bounded_review` 展开为有限 activation。环不会无限运行。不同轮次按顺序产生对象，角色之间通过实际路由和 ACL 交付；Coordinator 的控制元数据不代表它获得全部原文。Singleton 继续使用官方 Coordinator，不制造一个假 Singleton Worker。

预览不调用模型、不读工具、不登记交付。已退休作者的对象保留来源，通过持久存储交付时明确记录传输者是存储，不伪造退休 Agent 发消息。重复读取不会重复登记同一个交付收据。

### 2.3 需求与补做

同一个任务的必要需求是 AND；一个需求的明确提供路径可以是 OR。状态区分 `satisfied / planned / unknown / unsatisfied`。未来结果引用生产任务和输出槽，产物出现后才绑定真实 ID。

已有结果需要符合字段、检查记录和来源独立性条件，不能只靠 `low_trust=False`。重新查询和重新分工通过显式角色/补做契约进入同一个调度器，消耗同一预算；重新查询还要求真实读工具结果被输出引用。核验提案因缺少可信内容验收契约而明确拒绝，不把产物字段检查冒充语义核验。没有受支持提供者时会记录 `NO_SUPPORTED_PROVIDER`，不删除需求或强行交付。

**当前验收边界：**可执行的检查器是 `artifact_schema`，验证字段、引用和不确定项格式。它不判断结论在语义上正确。重查路径已有真实 ARE 读取验收；“不同方法核验”的语义标准、独立判分器及更强内容验收仍是 OPEN。生产解析器会直接报告 `OPEN_semantic_verification_requires_trusted_content_acceptance_contract`。不能因此声称实现了可靠任务完成判断。

### 2.4 联合选择与成本

新路径仍通过 `Controller.select_runtime_patch()` 进入 `joint_rewriter.py`，没有另建图运行时。候选包含 KEEP，最多 24 个，以不同结构/干预类别轮流入池，再按有效角色、方向边、能力、交付和协议去重；局部组合深度为 2。

比较权限、需求支持、疑似风险目标可达性和工作量。联合策略先限制结构风险不高于 KEEP，再保留非支配候选；`task_only` 不用风险优化选择，`task_plus_filter` 在任务选择后过滤，响应隔离不获得联合补做角色。固定组织在首轮后冻结结构族；后续新增角色实例不被悄悄当作固定组织执行。

`declared_work` 明确是请求步骤、输入字符和轮次代理，KEEP 也有剩余工作。输出 Token、工具返回量和任务后续扩展仍未知。`empirical` 有开发轨迹读取和多输出回归入口，预测输入/输出 Token、请求、重试和模型延迟，并保存误差与训练来源；没有校准文件、输入超出校准范围或预测不可用时显式报错，不换模式。

校准读取真实 v0.4 开发轨迹及后续 usage，拒绝离线夹具、费用不明运行、未独立确认任务完成的运行。没有从旧报告总计制造训练样本，也没有生成项目校准文件。模型分析、风险、角色和 Coordinator 都从同一 episode 总预算扣除，费率来自带日期的显式配置。生成/选择的 Python 耗时另外留痕；episode wall time 包含整条执行路径。

### 2.5 风险与真实接触

语义观测器复用现有模型客户端，输入只有可信目标、当前提案、实际外部证据和运行记录；正则是显式对照模式。风险失败会停止受影响路径。已修复 `subject_id` / `agent_id` 不一致，风险事件现在能触发联合入口（`risk_monitor.py`、`are_bridge.py`、`joint_rewriter.py`）。

历史记录采用单调 sequence，保留对象来源、实际交付和模型提交；未来输出只连接更晚的 activation，不连回过去产物。外部来源可达性、疑似风险未来可达性、语义采纳和环境后果分开。共享应用状态未绑定到对象版本的缺口明确列出；这不是完整模型内部因果图或安全证明。

输入区分 prepared、submitted、response 和实际 usage。预算拒绝不产生 submitted，不计实际模型接触。角色输入组件在组装时记录，重复文本精简保留各个来源身份；没有使用全文子串匹配判断 v0.4 的对象来源。官方 ARE Coordinator 的既有渲染仍有无法准确归因的部分，标为 unknown，见问题 V04-07。

### 2.6 攻击协议和数据清单

`attack.py` 新增 AttackCase 契约和 A/B/C/D、弱直接命令及正常指导等家族标签，复用已核验的邮件正文/环境通知入口。实际投递只发生在对应字段到达时，未读取就是未投递。新角色 ID 也可在同一个真实邮件工具边界接收该 overlay。新版本的模型接触评分使用 submitted 组件，不使用 prepared 冒充接触。

`experiment_suite.py` 实现固定迁移和 1/4/16 次黑盒协议的 harness API，冻结防御版本，保留每次载荷、父候选、受害者运行 ID、失败、反馈与分项费用；搜索与冻结评价日志分开。协议调用需要真实 victim/evaluator 接口，没有自动 mock。此次只有离线协议验收，没有执行攻击搜索。

**尚未具备四家族真实案例库。**当前独立后果适配只支持已核验的邮件状态规则；新增 A/B/C/D 的逐场景表面、记忆通道和合法完成判据尚未全部核验，不能把家族枚举写成四类攻击实验已经完成。研究矩阵不会自动加入未核验的攻击单元。

数据清单固定 60 个不同 Gaia2 任务 ID：20 开发、40 冻结评价。保留旧五例在开发范围。此前的筛选报告说明 adaptation/execution/time 共 480 行曾做静态解析，因此冻结部分只从未扫描的 ambiguity/search 抽取。选择只看 ID、分类和固定随机种子，随后计算原始字节哈希，不按攻击或防御结果筛选。不同任务 ID 不自动证明统计独立的环境。

已生成 420 个“60 任务 × 7 方法 × 1 种子”的无攻击 NOT_RUN 配置单元，保存在本地 `work/v0_4_matrix.json`。没有启动其中任何运行。数据清单与矩阵拒绝静默覆盖冻结文件。未知攻击结果保留在分母和上下界中，任务或危害未知时不计安全完成。

## 3. 完成矩阵

| 功能 | 已实现 | 生产路径接通 | 离线验证 | 真实模型验证 | 阻塞/证据 |
|---|---|---|---|---|---|
| 动态角色 | 是，契约/工具/输入/历史/生命周期 | 新配置路径 | 是，严格提案、角色差异及历史保留 | NOT_RUN | 超过单波活跃容量要求显式重规划，不静默截断 |
| 13 类拓扑及协议 | 是，小规模结构与有界协议 | 候选源与 GraphRuntime | 13 族参数化验收；不适用结构显式拒绝 | NOT_RUN | Singleton 沿用官方 Coordinator；是否适用由依赖/节点数决定 |
| 非中心数据交付 | 是 | 角色→角色实际对象交付 | Chain 后续角色收到前序结果 | NOT_RUN | ACL/有向路由硬检查 |
| 带补做的依赖修复 | 部分 | 已有对象/重查/角色补做接通 | planned→真实 ARE 产物→字段检查→交付 | NOT_RUN | 强语义验收、方法独立性仍 OPEN |
| 成本模式与完整记账 | declared_work 可运行；empirical 代码就绪 | 同一模型预算；回归模式需文件 | 拒绝未提交、缺校准；数值拟合夹具 | NOT_RUN | 无真实 v0.4 完成轨迹和校准文件 |
| 联合选择 | 是，有界候选与非支配比较 | 唯一 Controller 入口 | 结构/需求/风险选择路径 | NOT_RUN | 代理量不是成功概率或最终净收益 |
| 语义风险与历史/未来传播 | 是，有明确覆盖缺口 | 工具/角色输出/任务提案观测 | 注入响应、规范字段、时间方向验收 | NOT_RUN | 官方 Coordinator 部分输入来源未知；共享状态覆盖不全 |
| 攻击家族与预算协议 | 协议实现，案例适配部分 | 已核验的邮件/通知边界；黑盒 API 需真实 harness 回调 | 预算、反馈、未知后果分离 | NOT_RUN | 新家族案例与独立 evaluator 未齐全 |
| Gaia2/AgentDyn 适配 | Gaia2 是；AgentDyn BLOCKED | Gaia2 原接口；无假 AgentDyn adapter | Gaia2 本地读取；AgentDyn 导入真实失败 | NOT_RUN | AgentDyn 缺 deepdiff；能力元数据需核验 |
| 完整同状态恢复 | BLOCKED | 未接通 | 核对了真实源码 | NOT_RUN | ARE 状态导出不等于可恢复完整进程/队列/历史 |

## 4. 实际验收记录

执行过的命令包括：

```powershell
git status --short --branch
git rev-parse HEAD
git log -5 --oneline
.\.venv\Scripts\python.exe -m compileall -q security_adaptive_mas
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests
.\.venv\Scripts\python.exe -m security_adaptive_mas.run_framework --help
.\.venv\Scripts\python.exe -m security_adaptive_mas.run_framework --design-config configs/research_v0_4.yaml --mode adaptive --scenario scenario_universe_22_kb5ah9 --dry-run
.\.venv\Scripts\python.exe -m security_adaptive_mas.experiment_suite manifest --output configs/research_v0_4_manifest.json
.\.venv\Scripts\python.exe -m security_adaptive_mas.experiment_suite matrix --manifest configs/research_v0_4_manifest.json --design-config configs/research_v0_4.yaml --output work/v0_4_matrix.json
```

最终全量回归：**102 passed，1 warning，0 skipped，35.99 秒**。这包含原有 77 项和本轮 25 项参数化/行为验收，不把多次回归次数相加。警告来自上游 Pydantic 的 class-based config 弃用提示。AgentDyn 的真实导入失败单列为 BLOCKED，没有作为绿色 skip 隐去。

中间曾发现角色把自己的既有输出再作为新输入重复交付，相关测试失败；现已将自身输出登记为已见上下文，同时修正夹具生成重复引用的行为。未删除失败测试或放松生产 schema。全量回归后，对同一条需求验收补充了“planned 时拒绝敏感动作、满足后允许继续”的断言，并执行 `tests/test_v0_4.py::test_planned_dependency_waits_for_actual_checked_output`：**1 passed，0.39 秒**。它是同一测试的补充复验，不增加全量测试总数。

compileall、CLI help、新配置 dry-run、Markdown 相对链接和正常 Git 行尾设置下的 `git diff --check` 均通过。新的数据入口 dry-run 不导出场景、不下载数据、不推进任务。

全量数据生成 manifest 是本地读取与哈希，不是任务执行；没有下载数据或付费回放。AgentDyn 的网络操作只用于读取官方代码，见下文具体失败。数值拟合测试使用临时目录内明确标注的工程夹具，不能作为研究校准文件使用。

## 5. OPEN / BLOCKED 问题清单

### V04-01：AgentDyn 导入和能力目录 — BLOCKED

官方地址 `leolee99/AgentDyn` 重定向到 [SaFo-Lab/AgentDyn](https://github.com/SaFo-Lab/AgentDyn)，本地核对提交 `5353cf7615b135cace8d07c8f12dac53a16b6db3`，MIT 许可。源码提供 `get_suite(benchmark_version, suite_name)`、`BasePipelineElement.query(query, runtime, env, messages, extra_args)`、`FunctionsRuntime.run_function(env, function, kwargs, raise_on_error)`，TaskSuite 有任务环境注入和独立 utility/security 检查。

实际导入 suite 时沿 travel 注册链报 `ModuleNotFoundError: No module named 'deepdiff'`。Function 元数据也没有 ARE 的 write_operation 标记，不能根据工具名称猜只读性。受影响：AgentDyn 真实 adapter、任务/工具非付费验收及统一能力网关。Gaia2 部分不受影响。最小下一步是单独配置可导入的依赖环境并逐个核实拟用工具的副作用；本轮没有自动安装/修复依赖或造 mock adapter。

### V04-02：并发读 — BLOCKED

ARE `MessageQueue.get_by_timestamp()` 会从 PriorityQueue 取出消息并重新插入未到达项，Environment 另起事件线程。源码检查没有建立读工具、通知和时钟的共同原子契约。`concurrent_reads` 配置直接报错；顺序执行可独立使用。需要可信的应用/通知冲突契约后才能开放并发并验证原子预算，不以串行结果证明加速。

### V04-03：完整同状态恢复 — BLOCKED

ARE 有 `Environment.get_state()` 导出应用、事件队列等表示，current_time 被转成整数；未找到覆盖运行线程、通知消费位置、随机源、模型历史和 Controller 待执行工作的完整恢复接口。AgentDyn 的 env 深拷贝也不足以恢复本系统全部状态。未实现伪恢复；同时间点分叉消融配置会明确拒绝。下一步需可证明完整的快照/恢复协议。

### V04-04：内容核验和合法完成 — OPEN

当前产物检查器只验证字段与引用，TaskStatus 不因模型文字变 completed。没有可信的通用语义核验/完整合法任务 evaluator，原官方 success 单独保存。受影响：强核验补做、合法完成、可靠安全完成以及成本校准目标。最小下一步是为选定任务给出可信、独立且可执行的内容/状态检查，不让 LLM 自己宣布完成。

### V04-05：新增攻击家族实例 — BLOCKED / NOT_RUN

已有邮件/通知投递代码和邮件最终状态规则可复用；新家族的适用入口、持久读取链和独立目标判据尚未逐场核实。AttackCase 拒绝未核验的 surface/selector/evaluator。没有把现有邮件 case 改名凑四类结果。正常指导/报错等对照标签已在契约中，但没有伪造真实投递记录。下一步先核验实际表面和判据，再填入攻击矩阵；付费搜索需另行授权。

### V04-06：empirical 校准 — BLOCKED

没有真实 v0.4 已独立确认完成的开发 continuation 数据。已有五场总计不能展开成训练样本，临时工程拟合也不能当校准。当前开发配置明确使用 declared_work。下一步需真实开发轨迹、完成证据和版本化校准；没有文件时 empirical 拒绝启动。

### V04-07：官方 Coordinator 的完整输入归因 — OPEN

新角色与框架交付包在组装时登记对象 ID/版本和渲染内容，真实提交另记。官方 ARE 已有消息渲染中，部分工具/通知文本没有可靠对象 ID 对应钩子；当前明确记 unknown，不继续用子串匹配冒充精确归因。受影响：Coordinator 侧完整的逐对象接触/重复量分析。需要在其实际消息组装边界核验稳定标识。

### V04-08：固定组织和资源上限的适用条件 — UNSUPPORTED / OPEN

固定组织不允许后续悄悄添加新角色实例。角色重提、任务结构变化超出固定实例契约时明确报不支持。一次尚未处理的角色超过 7 个时要求显式重规划；不截掉任务。12 次有效图补丁上限包括阶段 ACL 更新，大团队多轮协议可能先达到它，代码不会自动提高上限。这些情况需要在正式矩阵中保留停止状态；上限是否足够是实验配置选择，不能为跑通在错误分支放大。

## 6. 修改清单和下一步

已有执行模块的主要变化集中于 `online_task.py`、`task_proposer.py`、`controller.py`、`topology_candidates.py`、`joint_rewriter.py`、`graph_runtime.py`、`dynamic_team.py`、`graph_evaluation.py`、`are_bridge.py`、`security.py`、`deepseek.py`、`performance_monitor.py`、`attack.py`、`run_framework.py`。新增四个聚焦模块：`organization_contracts.py`、`cost_model.py`、`risk_monitor.py`、`experiment_suite.py`；新增设计配置、实际任务清单和工程验收文件。README 只补当前状态和入口。

下一轮先 Review 角色/需求契约、敏感操作等待条件、固定组织边界与 12 次补丁上限，再确定独立任务/内容判据以及 AgentDyn 依赖与工具清单。只有这些问题解决、配置冻结并获得新的付费授权后，才执行真实开发验证、校准、攻击搜索和成对实验。本报告没有 ASR、Token 节省或安全收益结论。
