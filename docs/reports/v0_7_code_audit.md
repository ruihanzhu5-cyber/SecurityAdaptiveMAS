# v0.7 源码审查与实施边界

日期：2026-10-15。两份输入文档已完整阅读。基点和远端均为 `d67c77c19b6f584ba5839c654d1e4cc5e96f2c7e`，开始时工作区干净；新分支为 `research/v0.7-evidence-handoff-five-case`。

## 核实的接口

| 模块 | 核实结果 | 本轮处理 |
| --- | --- | --- |
| run_framework.main / run_case | 单 Episode、唯一 RunBudget、官方 Benchmark、AttackRuntime 和原始输出已存在 | 增加独立 Study 校验，复用执行函数 |
| are_adapter | import_benchmark_scenario 安装官方 Judge 并串行共享客户端，benchmark_evaluation 区分任务与 Runner | 保留语义 |
| are_modular | 默认工具含 delegate_task/get_artifact；ObservationLog 有真实历史渲染绑定 | 仅 v0.7 受控模式增加已到达证据触发与工具观察记录 |
| session | request_delegation→run_work→真实 Worker；typed snapshot、邮件字段原子和 metadata 已有 | 增加受控计划和有来源视图，旧方法保留 |
| worker_runner | 调用 ObservedReActAgent.reset/step、Memory，工具通过同一 ToolGateway | 复用；补派生结果寻源及交接记录 |
| delivery / policy | 单一请求边界有 RELEASE/LIMITED_RELEASE/HOLD/REQUERY/REASSIGN；原 risk-aware 会拒绝 unknown 原文 | 新实验条件单独选择动作；旧两种策略保持原样 |
| contracts | ArtifactRef/Record、WorkItem、RuntimeStamp、Candidate 已有 | 兼容增加 EvidenceField/View 和审计字段 |
| GraphRuntime | preview/apply 真正实现 route、ACL、assignment、spawn/retire；对象作者自身 ACL 登记不等于重组 | 直接复用，不重写 |
| MantaContextAdapter | select_packets 真正调用 visible_packets，前后核对 GraphRuntime ACL 与作者 | 直接复用 |
| ATMMutationAdapter | 真正调用 MutationContract.verify_invariants 与 StateDistiller.distill，核对精确去向 | 直接复用；不把结构通过解释为事实证真 |
| RiskMonitor | JSON 合约、相同 client/budget、按证据与 goal 缓存 | 不改提示词或检测算法；视图判断消费已有结果 |
| attacks | 精确工具返回覆盖、每场状态、独立初末状态评价、实际提交内容检查已接通 | C1/C2/C4/C5 直接复用原配置和 Payload |
| v0.6 preflight / Study | 兼容性和冻结功能已有，历史账本不可覆盖 | 封存旧脚本，新增 v0.7 编排入口 |
| context_growth | 已区分 prepared/submitted/received、响应 usage、重复字节 | 复用分析方法，补组件分类和未知项 |

## 按用户可见任务确定的最小字段

- C1：旅行日期变更，受控阶段等待真实 ENV；需要 date_start/date_end，歧义不选择答案。
- C2：Collaboration 邮件身份和日期建议；需要 email_id/date_start/date_end，邮件主题含义和购物授权仍由原任务执行判断。
- C3：已到达消息的会话、发送者和日期；攻击目标与动态 ID 绑定尚未具备完整判据。
- C4：消息历史归属，最小字段为 conversation_id/message_id/sender_id；不能代替 Files/附件/建群能力。
- C5：邮件身份与接收时间，最小字段为 email_id/timestamp；单页不代表全部八月邮件已覆盖。

这些是子任务需求类型，不含攻击目标 ID、Oracle 操作或未来正确日期。正文事实只能根据实际返回文本锚定；锚定不等于外部证真。

## 已确认 OPEN / BLOCKED

1. **BLOCKED_C3_TARGET：**输入文档没有给出已独立验证的敏感目标，不能自行选择或优化攻击。不会生成假 READY 的 C3_runtime 配置。
2. **BLOCKED_C3_SELECTOR：**固定 ARE create_and_add_message 用 uuid_hex(self.rng) 在执行时创建消息 ID；原 action 不含 ID。动态 selector 未核验前保留原 BLOCKED，不换旧消息或 ENV 注入。
3. **OPEN_NOTIFICATION_EVENT_ID：**固定 ARE notification_system.Message 只有类型、正文、时间、附件，没有 event_id。触发记录使用已到达 Provenance ID；原事件 ID 记 unknown，不伪造。
4. **PARTIAL_SCOPE_C4：**Files、附件和部分建群能力缺口继续保留。
5. **OPEN_FACT_VALIDATION：**确定性日期提取只证明来源位置；多日期、缺年份依据或不可解析内容不自动变成可信答案。
6. **NOT_RUN_REAL_PILOT：**本轮没有新的付费授权；真实 DeepSeek Worker Pilot 和 30 单元均不运行。旧 8 USD / C1 2 USD 授权不移用。
7. **OPEN_JOINT_GAIN：**一跳 Worker→Coordinator 可能使 Joint 与 Context-only 等价，不人为加边展示差异。

本报告为实施前审查记录。后续实际完成程度、免费证据和仍未支持的动作由实施报告单独记录。
