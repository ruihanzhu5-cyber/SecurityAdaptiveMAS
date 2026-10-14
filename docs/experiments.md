# 实验入口与证据口径（v0.5）

v0.5 的交付是工程路径验收，不是新一轮论文实验。付费执行状态为 `REAL_DEEPSEEK_RUN_NOT_AUTHORIZED`。

## 保留的真实历史证据

| 记录 | 用途 |
|---|---|
| [2026-10-13 日志](research-log/2026-10-13.md) | 官方 Single Agent 五场：success 5/5，三场出现 MaxIterationsAgentError；与后来 Controller 无关。 |
| [v0.1 五场框架报告](reports/framework_v0_1_five_case_report.md) | 当时版本的框架执行与停止记录。 |
| [v0.2 真实邮件运行](reports/framework_v0_2_paid_22_kb5ah9_2026-10-14.md) | 当时注入、接触、预算停止及最终邮箱状态。 |
| [v0.3 受攻击 Single Agent 对照](reports/v0_3_attacked_single_comparison_22_kb5ah9_2026-10-14.md) | 当时模型与同案例对照，保留原始成功口径。 |
| [Gaia2 筛选依据](reports/gaia2_screening_report.md) | 数据选择历史，不作为新攻击适用性验证。 |
| [v0.4 实施](reports/v0_4_implementation_report.md)、[v0.5 实施](reports/v0_5_implementation_report.md) | 代码和离线验收，不作为实际收益。 |

原文、日期、数字与结论不倒写；日志中因归档产生的相对链接作必要调整。旧阶段设计见[归档索引](archive/README.md)。

## 数据和计划

五场历史任务都属于 Development，不能再包装成未见测试集。现有 v0.4 manifest 为 20 个开发 ID 与 40 个冻结评测 ID，冻结评测来自此前未静态扫描的 ambiguity/search 分类。`experiment plan` 默认只用开发部分生成 140 条 NOT_RUN 计划，方法为 single、fixed、task_only、fixed_security、reactive_isolation、joint、task_plus_filter。

任务清单不是已验证攻击集。本轮只复用 `22_kb5ah9` 的邮件工具返回入口和独立 INBOX/TRASH 后果检查，做非付费回归；没有搜索新载荷、未执行矩阵。完整数据位置见[数据说明](../data/gaia2/README.md)。

## 每次运行保留什么

```text
output_dir/
  v0_1_cost_ledger.jsonl      # 沿用旧账本名，预留与结算
  <UTC时间_批次ID>/
    batch_config.json / batch_results.json / code_snapshot/
    <scenario_id>/<run_id>/
      run_config.json
      framework_trace.jsonl
      controller_decisions.jsonl / joint_decisions.jsonl
      topology_events.jsonl
      summary.json / evaluation.json
      runner.log / official_trace/
      failure.json          # 失败时才有
```

配置、代码版本及源码快照哈希可追溯。报告列出文件是否实际存在，旧结果缺 evaluation.json 不会回填到源目录。不同方法由配置中的 method 明确记录；未重新组织也应记录真实 KEEP，不编造拓扑事件。

## 各项结果分别解释

- `official_runner_success`：原生 Runner 字段，不改口径。
- `stop_reason` / `exception_type`：区分正常结束、预算拒绝、上游异常和不支持任务。
- `artifact_schema_valid` / `semantic_verified` / `legitimate_task_completed`：结构、内容和完整任务三个层次；后两者缺独立依据时 unknown。
- `attack_delivered` / 模型接触实例 / `target_attempted` / `gateway_blocked` / `attack_target_achieved`：分别记录投递、实际接触、操作尝试、拦截和最终后果。图可达性不是攻击成功。
- 请求、输入/输出 Token、未知计费上界、估算美元与耗时：均读取已有真实记录。费用采用历史估算费率，不冒充账单。预算预检拒绝不是实际提交请求。

report 不合成未知结果，不把缺失视为 false，也不把失败换来的低成本说成净收益。empirical、合法完成判据、四家族真实攻击和公平成对收益验证仍 OPEN / BLOCKED。
