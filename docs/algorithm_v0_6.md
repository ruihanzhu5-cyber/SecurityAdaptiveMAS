# v0.6 局部组织与交付规则

日期：2026-10-15。规则对应 `orchestration/policy.py`、`delivery.py` 和 `session.py`。这是待检验的工程策略，没有最优性或安全收益结论。

## 触发点和数据

只在具体委托和明确的结果消费请求处决策。WorkItem 保存具体指令、可用工具、已发放输入句柄和工作状态；ArtifactRef 为 `object_id@version:view`；DeliveryRequest 绑定真实调用者、工作、对象和所需字段。普通模型推理步骤不做全图搜索。

RuntimeStamp 包含 graph_version、在线观察序号、USER 目标修订和 object_epoch。新增对象/视图或 ACL 变化会改变 epoch。候选预览不运行工具或模型，提交前再次核对 stamp。

## 候选的实际含义

| 候选 | 生成条件 | 选中后 |
|---|---|---|
| DELEGATE | 指令、输入句柄、只读能力和实例资源检查通过 | 创建实例、分配具体工作，授权并交付指令 |
| RELEASE | 请求对象存在、字段可满足、申请资格有效 | 授权该对象及必要有向关系，交付实际载荷 |
| LIMITED_RELEASE | 已有确定性视图、请求字段足够、句柄允许该视图 | 只授权视图，不授权原正文 |
| HOLD | 可执行的等待 | 无正文 held 回执，原工作和句柄继续保留 |
| REQUERY | 同一工作有实际成功的可重放只读调用 | 重放原工具与参数，登记新结果，再次请求后审核 |
| REASSIGN | 原具体工作可交给新只读实例，修复和资源额度允许，引导来源未标可疑 | 提交新实例和工作承担者，执行新 Worker，不复制旧历史 |

一项消费请求最多六个候选，只有选中方案执行。REQUERY/REASSIGN 返回 repairing，不提前交付未来结果。一个 work 最多一次自动修复。无新请求时保持当前状态；HOLD 不被称为 KEEP 或任务成功。

## 共同可行性

有向图预览验证工具子集、对象版本、活跃/总实例和图补丁上限。ATM 真正调用 `MutationContract.verify_invariants` 和继承的 `StateDistiller.distill`，额外检查：

```text
child_tools ⊆ parent_tools - denied_tools
child_memory_refs ⊆ parent_transferable_refs
child_tools ∩ forbidden_tools = ∅
输入 Counter(ID@version:view) == 输出 Counter(ID@version:view)
每个输入恰好出现一次，输出去向与候选完全一致
```

父空集合同样检查；实际父节点来自当前生效图。ATM shadow_completed 不设为 true，结果只叫 structural_validation。所选路径消费的是已核对的精确 destinations，不能让共享区自动授予所有消费者。

## 两种方法的选择顺序

**modular_task_only**：可满足本次请求的交付 → 可执行补做 → 等待。同级依次比较新增模型步骤上界、立即交付字节、是否新实例和固定 candidate_id，不使用风险值。

**modular_risk_aware**：在相同候选上，先拒绝向可达受保护写能力的新消费者交付仍携带已标可疑载荷的方案；高权限接收者的 unknown 风险也不自动放行。再按相同任务与代价顺序选择。

email_metadata_v1 只能在确定性字段投影确实满足本次请求时替代 full。普通模型摘要没有该豁免。历史已提交的接触不从记录中扣除。

## 风险与成本的解释

同一对象、目标与提案的语义风险观察可使用本场缓存，两个方法相同。外部正文原子、元数据原子和分页字段有独立来源与字段路径；检测器只引用实际供给的证据，不读取攻击目标。

来源父链仍保留。本次没有携带正文，只证明这一次投影的字段内容；不证明整个来源可信。重查标 same_source，更换实例的未来输出仍未知。

候选中的步骤上界、字节数、新实例标记是代理。REASSIGN 的步骤上界包含 Worker 与其风险检查，不称为预测 Token 或已节省费用。真实用量来自同一 RunBudget 的 usage；缺 usage 的失败请求保留 unknown reservation。

预算由实际请求的字节上界、最大输出和已有费率估算进行预检；没有已校准的未来任务收益。合法任务完成没有通用独立判据，保持 unknown。
