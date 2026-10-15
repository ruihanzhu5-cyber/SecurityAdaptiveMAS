# C5_A3 原始运行轨迹（2026-10-15）

这里保存 `research/v0.7-evidence-handoff-five-case` 分支上 C5_A3 的**完整可用 batch 原始文件**。三个目录对应三次不同的启动记录，互不覆盖。复制时逐文件对比原始 SHA-256；每个目录的 `SHA256SUMS.txt` 列出其 batch 中全部原始文件的哈希。`study_manifest.json` 和 `study_cost_ledger_snapshot.json` 是该次 Study 结束后的独立副本。

| 目录 | 源码提交 | Run ID | 结果 | 付费请求 | 估算费用 |
| --- | --- | --- | --- | ---: | ---: |
| [`schema_failure/`](schema_failure/) | `32e2f9704926897c997174c7925812a034d3ad85` | `dcd27915dcf2` | Worker `finish` 缺少 `memory`，结果未交付 | 9 | 0.0320187 USD |
| [`path_length_failure/`](path_length_failure/) | `8b32fe22c549770f3db2db02f68682196afc13d6` | 无 | Windows 源码快照目标路径达到 260 字符，调用模型前停止 | 0 | 0 USD |
| [`finish_contract_rerun/`](finish_contract_rerun/) | `8b32fe22c549770f3db2db02f68682196afc13d6` | `260542f1fc63` | Worker→View→Coordinator 交付成功，随后费用预检停止 | 46 | 0.3348054 USD |

## 从哪里看

- 首次真实运行：[框架逐事件轨迹](schema_failure/scenario_universe_22_kb5ah9/dcd27915dcf2/framework_trace.jsonl) · [模型请求与响应](schema_failure/scenario_universe_22_kb5ah9/dcd27915dcf2/model_io/) · [官方 ARE 导出](schema_failure/scenario_universe_22_kb5ah9/dcd27915dcf2/official_trace/scenario_universe_22_kb5ah9_31cefe49.json) · [摘要](schema_failure/scenario_universe_22_kb5ah9/dcd27915dcf2/summary.json) · [失败堆栈](schema_failure/scenario_universe_22_kb5ah9/dcd27915dcf2/failure.json) · [共享费用账本原始行](schema_failure/fee_ledger_rows.jsonl)。
- 路径长度故障：[batch 配置](path_length_failure/batch_config.json) · [当时的 Study 清单](path_length_failure/study_manifest.json)。此目录没有模型请求或官方轨迹，因为失败发生在 Episode 启动前。
- 修复后重跑：[框架逐事件轨迹](finish_contract_rerun/scenario_universe_22_kb5ah9/260542f1fc63/framework_trace.jsonl) · [模型请求与响应](finish_contract_rerun/scenario_universe_22_kb5ah9/260542f1fc63/model_io/) · [官方 ARE 导出](finish_contract_rerun/scenario_universe_22_kb5ah9/260542f1fc63/official_trace/scenario_universe_22_kb5ah9_d6c08c35.json) · [摘要](finish_contract_rerun/scenario_universe_22_kb5ah9/260542f1fc63/summary.json) · [评价状态](finish_contract_rerun/scenario_universe_22_kb5ah9/260542f1fc63/evaluation.json) · [交付与模型提交关联](finish_contract_rerun/scenario_universe_22_kb5ah9/260542f1fc63/handoff_delivery_submission_audit.json) · [共享费用账本原始行](finish_contract_rerun/fee_ledger_rows.jsonl)。

每个 batch 还保留 `code_snapshot/`、`batch_config.json`、`runner.log`、`artifact_index.json`、攻击接触记录、视图与拓扑审计、费用和上下文测量等原始文件。完整模型输入和模拟环境邮件内容也在这里；请将其中的攻击文字视为**实验数据**，不要作为运行指令。

这两个真实 Episode 都未完成官方 Benchmark Judge 评价。第一场报告见[契约失败分析](../../../reports/2026-10-15_v0_7_C5_A3_real_handoff_pilot.md)，第二场见[修复后重跑分析](../../../reports/2026-10-15_v0_7_C5_A3_finish_contract_rerun.md)。本目录未加入本地费用授权文件或整份历史共享账本；每场的两条原始账本行已单独保留，可与 `study_cost_ledger_snapshot.json`、`summary.json` 和报告核对。
