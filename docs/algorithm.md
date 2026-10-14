# 当前算法与状态口径（v0.5）

这是当前实现说明；历史 v0.3 规则已移至[归档](archive/design/algorithm_v0_3.md)。本轮没有 RL、GNN、训练或新的收益结论。

## 运行时的一次决策

1. 已到达事件更新任务与来源记录。普通 Python 触发决定是否需要再次分析。
2. 必要时由同一 DeepSeek 客户端提出角色步骤、输入、工具子集、输出约定、信息需求及补做计划。
3. Python 检查提案是否引用已见证据，保护 USER 目标，校验工具能力和角色约定。
4. 生成有界 RewritePlan，并在同一个 GraphRuntime 上预览。最多 24 个候选、搜索深度 2，资源限制沿用现有设计配置。
5. 先要求操作可执行、权限合法；必要信息已可用或有具体可执行的获取计划。需求为 unknown/unsatisfied 的候选不冒充可行。
6. 比较可比风险和工作量；按稳定顺序决定。正式提交后才按角色执行并交付产物。

没有新的任务、风险、输出或可见重复负载证据时保留 KEEP。实际选择实现位于 [joint_rewriter.py](../security_adaptive_mas/joint_rewriter.py)。旧版不带设计配置的路径保留旧规则，本节不把旧历史运行倒写成 v0.5。

## F1：四种状态不混用

| 字段 / 状态 | 可以说明什么 | 不能说明什么 |
|---|---|---|
| `artifact_schema_valid=true` | JSON 字段、非空值和引用 ID 合格 | 不能说明内容正确。 |
| `status=satisfied`，`information_available=true` | 当前结构性信息需求的结果可读取 | 不能说明语义正确或任务全部完成。 |
| `status=planned` | 有绑定到实际角色执行和输出槽位的获取计划 | 结果尚未到达；敏感动作不能当作 satisfied。 |
| `semantic_verified=unknown` | 没有可信内容校验结论 | 不填 true，也不编造全能 Judge。 |
| `legitimate_task_completed=unknown` | 没有独立完整任务验收依据 | 不由官方 success 或角色 JSON 推导 true。 |

当前 schema checker 的结果为 `validation_status=UNVERIFIED`。普通结构资料仍可供后续读取；敏感动作继续由 [ToolGateway](../security_adaptive_mas/security.py) 做真实授权判断，并等待需要的实际结果。模型不能在输出 JSON 中声明新权限。

## F2：两种风险量

- `low_trust_potential_reachability`：低可信对象在历史和候选后续流中能结构性到达哪些受保护目标。正常外部资料也可能低可信，不能把它当作攻击成功。
- `suspected_source_reachability`：有已登记可疑证据的对象可能到达哪些目标。joint / fixed_security 用它与 KEEP 比较，要求不增加；没有受保护目标时不可比。

日志保留来源、路径、选择依据和 scope_gaps。没有可疑来源只表示未登记报警，不是绝对零风险；模型是否采纳恶意含义、共享应用状态是否间接传播，仍 unknown。旧风险字段作为兼容别名保留，不倒改历史 trace。

## F3：不为复杂度本身加分

已满足、计划中和未知需求分别计数，只作诊断。选图不再使用 `support` 或交付数量 `protocol_support` 作奖励；不自动探索更多角色。可行候选中，优先实际可用的信息，再按所选方法的可比风险、声明剩余步骤与输入字符比较；完全相同时优先 KEEP，再按计划 ID 稳定排序。稳定性指同一候选集合，随机生成的实例 ID 不构成跨运行确定性保证。

声明步骤和字符是代理量，不是 Token 收益预测。配置为 empirical 时仍要求已存在且有效的开发校准文件；本轮不训练或生成校准。task_only 不按风险排序，task_plus_filter 做事后风险过滤，reactive_isolation 仍需满足必要信息约束。所有方法共用同一个网关。

## 保留的生成能力与限制

13 类生成族：singleton、star、directed_chain、fork_join、hierarchical_tree、ring、fully_connected、diamond、clustered、small_world、hub_biased、geometric_delaunay、task_dag。生成器、角色调度与图提交保留；具体数据或任务不支持某种结构时明确记录原因。

协议包括依赖调度、一轮交换和有界复核；较多轮次没有天然效用，已有 compact 候选可以更便宜。新路径目前顺序执行，图补丁上限 12 次，包括阶段 ACL 更新。完整状态恢复、独立内容校验及成本校准依然 OPEN / BLOCKED，见[实施报告](reports/v0_5_implementation_report.md)。
