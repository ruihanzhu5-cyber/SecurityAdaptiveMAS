# v0.7 Evidence Handoff 实施记录

日期：2026-10-15。基点：`d67c77c19b6f584ba5839c654d1e4cc5e96f2c7e`。分支：`research/v0.7-evidence-handoff-five-case`。

## 1. 这次做了什么

以前的真实实验中，模型可能一直自己执行，不创建 Worker。这样即使代码里有图和视图，也没有真实的信息交接可供比较。本轮新增默认关闭的受控交接模式：等合法任务消息到达，在指定阶段启动一次只读 Worker，让它自己生成工具调用和结果，再通过真实 `get_artifact` 交给 Coordinator。旧 v0.6 自主委托模式保留。

本轮完成代码与免费接线核验。**没有调用真实 DeepSeek API。真实 Pilot 为 NOT_RUN，尚未达到任务书最后要求的真实模型验收门槛。** 当前不能报告安全或效率收益。

## 2. 实际输入输出链

```text
官方 ARE 的 USER / ENV（必须已到达）
  │
  ├─ 原 Coordinator 继续执行
  └─ v0.7 ControlledHandoffPilot：一次受控、只读委托
       → 原 DelegateTaskTool / WorkItem / run_work
       → AOrchestra reset / step / Memory
       → Worker 的真实工具动作 → 原 ToolGateway → ARE App
       → 原 AttackRuntime 在声明的一个返回字段执行一次覆盖
       → typed snapshot + Provenance Artifact
       → EvidenceViewBuilder：来源字段、日期锚点、缺项与冲突
       → Worker finish 派生结果（自由文本不自动成为事实）
       → 真正调用 get_artifact
       → DeliveryService 生成条件允许的候选
       → GraphRuntime.preview + ATM 精确去向验证
       → Policy 选择 → GraphRuntime.apply
       → Graph ACL + MANTA.visible_packets + MessageGateway
       → 官方 ObservationLog 渲染选中结果
       → Coordinator 实际 model_request_submitted / model_io
       → 官方 Benchmark Judge → 官方评价 / 生命周期收尾

所有模型角色 → 同一个 DeepSeekClient / RunBudget / 历史共享费用账本
EvidenceExtractor 是确定性 Python，不调用模型
```

受控结果使用 ObservationLog，明确标为不可信工具观察，不添加额外 USER 或 ENV，也不伪造官方事件 ID。固定 ARE 的通知没有 event_id，因此保留 unavailable，另记真实的触发 evidence_id。

## 3. 文件改动与类别

| 文件 | 用途与实际修改 | 类别 |
| --- | --- | --- |
| `orchestration/handoff_pilot.py` | 只根据已到达消息创建一次计划，调用已有 delegate/get_artifact；不读取未来队列和 Oracle | 受控实验触发 |
| `configs/handoff_requirements_v0_7.yaml` | 五例自然任务字段和只读工具；不含攻击目标、隐藏答案、强制页码 | 实验需求声明 |
| `orchestration/contracts.py` | EvidenceField / EvidenceView，精确 ref、路径、span、状态 | 数据契约 |
| `orchestration/evidence_views.py` | 从真实 typed snapshot 和派生来源提取有限标量；私有保存锚点；冲突保留缺项 | v0.7 证据选择方法 |
| `orchestration/session.py` | v0.7 开关、实际委托启动、视图登记和真实句柄别名 | 运行时接线 |
| `orchestration/worker_runner.py` | Worker finish 后寻源生成视图；不改变原 Worker 生成与工具循环 | 运行时接线 |
| `orchestration/delivery.py` | 同一交付边界生成 FULL/VIEW/HOLD，继续验证 ATM、路由和 ACL；记录载荷 hash | v0.7 交付决策与审计 |
| `orchestration/policy.py` | 新条件允许动作与排序；旧 method 的分支保留 | v0.7 选择规则 |
| `orchestration/configuration.py` | 新配置显式启用 v0.7，v0.6 不接受实验字段 | 配置隔离 |
| `adapters/are_modular.py` | 将真实受控结果接入原历史渲染和提交归因 | ARE 接线 |
| `run_framework.py` | 仅新 Study 支持统一 160 请求/2M Token；新授权、冻结和历史账本校验，复用原 run_case | 费用与编排基础设施 |
| `scripts/run_evidence_handoff_study.py` | preflight / prepare / 单单元 run / analyze | 实验编排 |
| `scripts/analyze_evidence_handoff.py` | 复用原上下文分析，增加角色成本、视图、提交关联和拓扑分类 | 离线测量 |
| 四个 `tests/test_v0_7_*.py` 文件 | 真实接口免费回复夹具、视图、条件差异、费用契约 | 免费测试 |

本轮确实新增了 v0.7 的研究选择规则；不能把这些修改全部称为基础设施。旧 v0.6 条件下没有启用新选择规则。GraphRuntime、MANTA、ATM、AOrchestra 源码、ToolGateway 权限、RiskMonitor 提示词、Coordinator 协议、已有攻击配置/Payload、Judge 评价规则与计费算法均保留。

## 4. 条件实际支持到哪里

| 条件 | 当前实现 | 边界 |
| --- | --- | --- |
| N0 / A0 | 原文 Artifact 经正式 FULL_RELEASE | Worker finish 没有复制攻击文字时，不人为补入攻击；必须查实际 model_io |
| A1 | 同一个 builder 的任务字段视图，缺项或冲突时 HOLD | 没有自动补答案，也不把 HOLD 当任务成功 |
| A2 | 不交付任务 Evidence View；风险不明/支持时拒绝向高权限者交付全文；可撤销已有边/ACL | 没有已有路径时，风险图补丁为 0；不强制新建替代 Worker |
| A3 | 先选择可用视图；无法交付且有传播路径时，撤销实际路径/ACL | 一跳交接中可能与 A1 完全相同，免费夹具即出现这种情况 |
| A4 | 条件与阻断记录已接入，但不能运行独立恢复 | BLOCKED：未声明独立合法来源；同源一次性注入消失不等于独立恢复 |

没有追加二段人工团队子试验，没有实现通用语义去污、复杂预测、RL、全局重组或自动寻找独立替代来源。新条件目前缺项就 HOLD；没有证据支持某个重查一定补足字段时，不自动反复重查。后续 Coordinator 仍可依原工具权限继续合法任务。

## 5. 证据视图的含义

typed 表示值确实来自当前类型对象中的字段；anchored 表示日期能定位到原文范围。两者都不等于外部证真。Worker 自由文本即使声称某个日期，也不能直接成为 view 的事实。

接收者只看到白名单中的日期、有限 ID 和数值。原文、注入段落、Worker 自由叙述和 source_span 不进入该视图正文；原始引用与 span 留在本地私有审计记录。来源仍是 low_trust 的派生链，不提升为 trusted_task。

解析只覆盖有限英文月份日期、日期区间和 ISO 格式；年份未写明时保留 unknown，不使用仿真时钟补年份。不同日期无法消歧时整项 conflict，所需字段不足就 HOLD。字段数量超过有限容量时明确 capacity_exceeded，而不是截去困难字段后宣称完整。字段覆盖只表示这个读取子任务的声明字段，单页邮件不等于整个合法任务完成。

## 6. 免费测试与实际证据

完整免费回归 **90 项通过、3 个同类上游 Pydantic 弃用警告、184.97 秒**，涵盖 v0.7、旧模块、原攻击 Harness、预算授权、portable、官方 Benchmark/Judge 和生命周期。随后对缺年份、非 full 父引用、空字段/旧图请求和真实来源覆盖门槛做修订，最终专项 **17 项通过、3 个同类警告、21.12 秒**。最后补齐初末图/逐行审计后，真实免费 C5 Joint 集成 **1 项通过、3 个同类警告、41.48 秒**。这些轮次不相加为独立测试总数；最终两次修订没有重新执行全部 90 项。

Ruff、compileall、git diff --check 均通过；Markdown 检查 273 个本地文件/图片目标，0 个缺失。检查器不验证标题锚点，也不声称已在浏览器逐页审阅 GitHub 渲染。

本地原始测试记录：`work/v07_final_tests_01.log`、`work/v07_refine_tests_02.log`、`work/v07_audit_final_01.log`，以及对应 `work/pytest_v07_*` 原始免费 Episode。它们继续留在忽略目录，不上传合成模型请求或原始正文。

阶段记录：P0 两项通过；P1 六项通过；P2 交接/视图/旧模块 22 项通过，已有路径撤销三项通过；P3 真实免费轨迹分析一项通过；P4 编排与 portable 18 项通过。它们是不同时间的重叠验证，**不能相加当作独立测试总量**。

免费模型回复明确是测试夹具。官方 ARE/App、AOrchestra、WorkItem、MANTA、ATM、GraphRuntime、get_artifact 和保存的模型请求实际走过，但这不证明 DeepSeek 的自主行为或防御效果。夹具故意不完成 C5 全部合法删除任务，因此 Judge 的负结果不能当真实模型能力结果。

## 7. 测量口径与账本

每角色保存请求、输入/输出 Token、响应延迟、估算费用和未知费用上界。请求前 UTF-8 字节上界与响应实际 Token 分开保存；重复组件字节有嵌套与渲染差异，明确非可相加 Token 归因。EvidenceExtractor 额外模型请求为 0，其 Python 延迟未单独计时，写 unknown。

交付审计保存真实作者、接收者、ref/view、graph_version、payload hash、字节数，并关联实际提交请求的同一 component hash。来源关联、已提交文字、语义采纳、危险动作和最终环境后果仍分别判断；精确载荷不存在不证明所有语义影响消失。

delegation 的 spawn/assignment/初始 route 是受控实验 scaffolding；首个合法交付的 route/ACL 也单列。只有实际撤销等风险驱动补丁才记 risk_driven_patches，不把候选或 KEEP 算有效重组。

旧账本保留在 `D:/SecurityAdaptiveMAS-v0.6/results/runs/v0_6_modular_pilot/20261015_01/v0_1_cost_ledger.jsonl`。当前计入历史费用与未结算预留合计 **1.3179412 USD**，其中历史未结算 0.40 保留。新增 8 USD 只是待批准计划；本轮新增付费调用为 0。旧 C1 2 USD 或旧 Study 8 USD 不适用于新 Study。

## 8. 未完成和下一步

1. **NOT_RUN_REAL_PILOT：**新的付费授权尚未收到，真实模型验收未完成。
2. **BLOCKED_C3：**独立攻击目标和动态消息 selector 未确定，不创建假 READY 配置。
3. **BLOCKED_A4：**独立合法恢复来源未声明，五个 A4 均阻断。
4. **PARTIAL_SCOPE_C4：**Files/附件/建群缺口保留；supported_subtask_utility 没有独立 checker，写 unknown，不用全任务成功率代替。
5. **OPEN_FACT_VALIDATION：**来源锚定不证明事实正确；日期冲突、不支持的格式、跨来源歧义无法自动解决。
6. **OPEN_JOINT_GAIN：**自然一跳图未证明图控制的独立收益；不为制造差异加边、强制改派或创建 Worker。
7. **OPEN_REPAIR：**未实现新条件自动推断独立来源或有效 REQUERY；缺项时保留 HOLD。
8. **OPEN_EXPOSURE：**Coordinator 可能先读取固定攻击来源；first_actual_reader 不保证 Worker 首先中招，必须逐场核验。
9. **OPEN_CONTEXT_ACCOUNTING：**官方未知渲染、组件重叠、无独立子任务 checker 的开销不能精确归因；没有提前停止所产生的“效率收益”结论。
10. **BLOCKED_REQUIRED_FIELDS：**真实初始 C1 注入消息是普通聊天，C2 注入邮件是合作邀约，两者无可解析日期。C1 ENV 触发阶段的完整视图尚未核验；C2 初始触发要求日期与实际来源不匹配。C1_A1/C1_A3/C2_A1/C2_A3 当前都 BLOCKED，不仅仅缺费用授权。最小可选方案是先独立核验真实 ENV 后的合法日期来源，或经用户确认调整相应合法子任务需求；本轮不改触发、删字段或填答案来通过验收。

下一步使用重新冻结的 `C5_A3` 作为首个 READY Pilot，明确授权仅此一场的费用，再核验真实 Worker→handoff→下游提交→官方 Judge 链路。`C1_A3` 的字段覆盖问题仍未解决，继续 BLOCKED。A2/A4 属第二阶段，需要另行授权；当前 A4 即使授权也仍阻断。

## 9. 命令与冻结

| 阶段 | Git 提交 |
| --- | --- |
| 实施前审查 | `0d8b3bc` |
| P0 真实接口受控交接 | `7d574ce` |
| P1 来源锚定视图 | `9ba270b` |
| P2 条件选择与真实交付 | `9cbe5f3` |
| P3 成本与提交审计 | `01c9848` |
| P4 编排、授权和阻断门槛 | `8371f2f` |
| 初末图与逐行审计补充 | `88e9fd4` |

最终生产代码提交为 `88e9fd410f43a6355160297858d0e8a968b69cbb`，源码快照 SHA-256 为 `f6aa6f1dbc8a8b5b816f77c24b489e6a988af9f27b0cb83033842d6b669425c6`。文档另行提交，之后在干净 HEAD 上 prepare 冻结；清单中的实际 code_revision 为本次运行依据。

实际生产 diff 存于本地 `results/runs/evidence_handoff_v0_7/free_verification_20261015_final/production.diff`，SHA-256 为 `2ca1e58336d5dbbc3d1736449a221dea770eabb0aedee27963859fbc5e4b5e11`。可复核：

```powershell
git diff d67c77c19b6f584ba5839c654d1e4cc5e96f2c7e 88e9fd410f43a6355160297858d0e8a968b69cbb -- security_adaptive_mas scripts configs
```

本地源码工作区沿用 `D:/SecurityAdaptiveMAS-v0.6-benchmark-runtime`，研究分支已经变为 v0.7；目录名并不代表 Git 分支。venv 复用 `D:/SecurityAdaptiveMAS-v0.6/.venv`。不能只从目录名判断实验版本。

```powershell
Set-Location D:/SecurityAdaptiveMAS-v0.6-benchmark-runtime
$env:SECURITY_MAS_ROOT=(Get-Location).Path
$env:PYTHONPATH=$env:SECURITY_MAS_ROOT
$python='D:/SecurityAdaptiveMAS-v0.6/.venv/Scripts/python.exe'
$study='results/runs/evidence_handoff_v0_7/your_new_study_id'
$ledger='D:/SecurityAdaptiveMAS-v0.6/results/runs/v0_6_modular_pilot/20261015_01/v0_1_cost_ledger.jsonl'

# 两步均免费；使用新的、不存在的目录，避免覆盖原始记录。
& $python -X utf8 -m scripts.run_evidence_handoff_study preflight --study-dir results/runs/evidence_handoff_v0_7/your_preflight_id
& $python -X utf8 -m scripts.run_evidence_handoff_study prepare --study-dir $study --ledger $ledger
& $python -X utf8 -m scripts.run_evidence_handoff_study analyze --study-dir $study

# 本轮首个 Pilot 为新清单中明确记录且免费预检 READY 的 C5_A3。
# 收到绑定新 Study、源码快照、计划哈希、单场 0.40 USD 的新授权后才运行。
& $python -X utf8 -m scripts.run_evidence_handoff_study run --study-dir $study --study-unit C5_A3 --authorization "$study/fresh_user_authorization.json" --acknowledge-model-cost
```

授权文件必须含 `kind=v0_7_fee_authorization`、本 Study 的 `study_id`、冻结的 `code_snapshot_id` 与 `plan_sha256`、新的 `user_confirmation`、`additional_cap_usd=0.40`、`authorized_units=["C5_A3"]` 以及 `phase2_authorized=false`。prepare 不创建有效授权文件。旧授权不能移用；即使代码可配置至 8 USD，也不能替用户批准 8 USD。

每场 `.claim.json` 独占创建，预算预留在原 ledger 中追加，完成后追加结算；异常中断留下原预留。已经尝试的单元不能自动重跑；发生接线、Judge、费用、攻击未送达或实际交接缺失时阻止后续运行。冻结的源码、测试、五例哈希、原 Payload、字段清单、路径、配置与账本历史前缀都有记录。新 Study 使用统一 160 请求、2,000,000 Token、0.40 USD、20 turns、idle 默认关闭；旧默认 100/800,000/0.40 不变。增加请求/Token 上限并不放宽单场费用。

## 10. 关联文档

- [实施前源码审查](v0_7_code_audit.md)
- [五例免费预检](v0_7_five_case_preflight.md)
- [真实 Pilot 状态](v0_7_pilot.md)
- [历史攻击 Harness](v0_6_attack_harness_implementation.md)
