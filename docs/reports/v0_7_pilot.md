# v0.7 Evidence Handoff 真实 Pilot：NOT_RUN

日期：2026-10-15。分支：`research/v0.7-evidence-handoff-five-case`。

## 实际状态

**本轮真实 DeepSeek Pilot 未运行。** 任务书明确要求新 Study 另行确认费用，旧 8 USD Study 与旧 C1 2 USD 授权不能移用。本轮没有新授权，没有向 DeepSeek 发起请求，也没有追加历史费用账本。

| 项目 | 本次真实数据 |
| --- | --- |
| 真实 Episode / Run ID | NOT_RUN |
| 真实 Worker 创建、读取和 finish | NOT_RUN |
| 真实攻击注入、模型接触、危险调用和目标后果 | NOT_RUN |
| 实际 get_artifact、MANTA 交付、下游模型提交 | NOT_RUN |
| 真实拓扑调整和 ACL 改动 | NOT_RUN |
| 官方 Benchmark 任务评价 | NOT_RUN |
| Coordinator / Worker / RiskMonitor / Judge Token 与延迟 | NOT_RUN |
| 新增付费模型请求 | 0 |
| 新增真实 API 估算费用 | 0 USD |

免费夹具的响应 Token 数是测试固定值，不填入这张真实实验表。历史 Single Agent 5/5、此前四组 Pilot 和此前真实 C1_B1 也不是 v0.7 的成绩。

## 已具备和仍需确认的条件

- 默认关闭的受控 Worker→真实工具→Artifact→get_artifact→MANTA/ATM→后续模型输入链通过免费接线。
- 五例官方 Benchmark 预处理通过；C3 攻击与全部 A4 独立恢复仍 BLOCKED。
- 字段解析有严格边界，缺年份、多日期冲突和缺项不能自动补齐。C1_A1/C1_A3/C2_A1/C2_A3 当前因字段覆盖问题 BLOCKED；详见逐例预检。
- 新 Study 计划上限 8 USD、单场 0.40 USD，只是待批准计划，不是当前授权。
- 冻结预算为 160 请求、2M Token、20 ARE turns、idle 默认关闭。每角色和 Judge 使用唯一共享 RunBudget；历史未知费用预留仍计入。
- 新费用授权必须绑定本次 code_snapshot_id、plan_sha256 和允许单元。入口每次只执行一单元，不提供自动跑 30 条的操作。

初次真实验证入口为 `run --study-unit C1_A3`。当前尚不具备直接启动剩余矩阵的条件；先解决免费预检中相关证据覆盖疑问，再获得新的明确费用授权。真实 Pilot 还须观察 Worker 是否确实接触攻击来源、下游是否消费选中视图和 Judge 是否有效。没有交接或缺注入证据时，不能报告已经验证攻击防御。

关联：[源码审查](v0_7_code_audit.md) · [实施说明与命令](v0_7_implementation.md) · [逐例预检](v0_7_five_case_preflight.md)
