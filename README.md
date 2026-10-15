# SecurityAdaptiveMAS · v0.7 受控证据交接开发分支

> **2026-10-15 v0.7：代码与免费核验已完成，真实 DeepSeek Pilot 为 NOT_RUN。** 新增默认关闭的受控 Worker 交接、带来源的字段视图、全文/视图/图控制对照和 30 行独立实验清单。五例官方 Benchmark 均能免费预处理。最终 17 行可准备但未运行、13 行 BLOCKED；C1/C2 日期视图缺口、C3 攻击目标与动态绑定、A4 独立恢复来源均明确留档。尚无 v0.7 安全或性能收益结论。

[v0.7 实施说明与 PowerShell 命令](docs/reports/v0_7_implementation.md) · [逐例预检与阻断](docs/reports/v0_7_five_case_preflight.md) · [真实 Pilot：NOT_RUN](docs/reports/v0_7_pilot.md) · [源码审查](docs/reports/v0_7_code_audit.md)

新入口：`python -m scripts.run_evidence_handoff_study preflight|prepare|run|analyze`。输出为忽略的 `results/runs/evidence_handoff_v0_7/<study_id>/`。`run` 每次只接收一个 `--study-unit`，需要新费用授权；旧 8 USD/2 USD 授权不移用，当前 C1_A3 还有字段覆盖阻断。原始轨迹、模型输入与历史账本留在本地。

以下为保留的 v0.6 历史开发与实验记录，属于原版本和原运行条件。

> **2026-10-15 五例 Study 更新：**免费验收 36 项通过，20 行计划中实际启动 1、NOT_RUN 17、攻击入口阻断 2。C1_B1 的 12 次模型请求均已返回，但回合结束后 ARE 无时限等待通知，Study 按规则停止。没有完成的 Episode 或安全/性能结论。见 [五例预检](docs/reports/2026-10-15_v0_6_five_case_study/five_case_preflight_report.md) 与 [20 单元实际报告](docs/reports/2026-10-15_v0_6_five_case_study/five_case_20_unit_pilot_report.md)。

以下四组 pilot 是此前独立实验记录，保留其当时事实。
**让任务执行带动分工，在结果交付时处理风险。**

本项目研究长任务中的多 Agent 协作：主 Agent 怎样提出具体委托，子 Agent 怎样按需使用前序结果，遇到可疑外部内容时怎样调整交付对象和工作承担者，并记录这些选择的真实代价。

> **当前阶段：v0.6 四组真实 pilot 已运行，均因 Token 预算预检停止。** Issue #3 的 JSON 契约已修复；19 项免费检查通过。四组共 98 次真实请求全部成功响应，但都没有 Worker 或图重组，两个攻击组的载荷均未送达，完整任务完成仍为 unknown。不能据此宣称动态组织或安全/性能收益。详见 [四组真实实验报告](docs/reports/2026-10-15_v0_6_issue3_fixed_four_group_real_pilot.md)。

> **历史记录保留：**此前 [JSON mode HTTP 400 失败](docs/reports/2026-10-15_v0_6_B1_json_mode_http_400.md) 与 [固定清单启动阻塞](docs/reports/2026-10-15_v0_6_issue3_fix_and_four_group_preflight.md) 分别保留。用户批准新批次入口后，本轮沿用原共享账本，新增费用估算 0.7701927 USD；含此前记录的账本合计约 0.7909167 USD。

[实际架构](docs/architecture_v0_6.md) · [局部决策规则](docs/algorithm_v0_6.md) · [实施与问题台账](docs/reports/2026-10-15_v0_6_implementation.md) · [上游源码与许可](docs/upstream_v0_6.md) · [历史研究日志](docs/research-log/README.md)

## 系统现在怎样运行

```text
已经到达的 USER / ENV 消息
  → 官方 ARE Coordinator 正常执行
  → delegate_task：具体指令、已发放的输入句柄、只读工具
  → AOrchestra 派生 Worker 在同一 ARE 环境中执行
  → 保存结果和来源，返回无正文的状态回执
  → Coordinator 或另一个 Worker 调用 get_artifact
  → 生成局部候选 → ATM / 有向图预览 → 策略选择
  → 提交路由、对象 ACL 或工作改派
  → MANTA 筛选 → 最终权限核对 → 实际模型输入
```

只有一份生效图、一个应用环境和一个模型费用账本。MANTA 的图是只读筛选视图；ATM 检查的是结构和对象去向。它们都不能独立证明内容正确或模型没有受攻击。

## 与 v0.5 的区别

v0.6 不在执行前分析全局 NeedContract、生成角色并枚举整张拓扑。Coordinator 先做事，需要协作时再提出委托；具体结果需要交付时，再决定原样交付、字段视图、暂缓、重查或改派。

Worker A 的结果可以由 Worker B 自己请求，形成有向的 A→B→Coordinator 关系。委托回执只包含句柄，不提前返回正文。邮件元数据视图从真实工具返回中提取 ID、时间和分页字段，不包含邮件正文，也不会把外部来源改成可信来源。

## 本地入口

本轮开发目录为 `D:\SecurityAdaptiveMAS-v0.6`，分支为 `research/v0.6-modular-runtime`。旧目录与旧实验留在 `D:\SecurityAdaptiveMAS`。新目录有独立 `.venv`，数据和 ARE 固定源码通过显式路径读取。

```powershell
Set-Location D:\SecurityAdaptiveMAS-v0.6
.\.venv\Scripts\security-mas.exe doctor --config configs/research_v0_6.yaml
.\.venv\Scripts\security-mas.exe run --design-config configs/research_v0_6.yaml --mode adaptive --scenario scenario_universe_22_kb5ah9 --task-scope-policy sandbox_permissive --dry-run
.\.venv\Scripts\python.exe -m pytest tests/test_v0_6_modular.py -q --basetemp work/pytest_v0_6
```

CLI、`python -m security_adaptive_mas` 和 Python API 都调用同一个 `run_framework.run_case`。旧设计配置请在 v0.5 worktree 使用；新路径不会失败后自动切回旧算法。

新机器需要设置忽略的 `work/v0_6_local_paths.yaml`，填写 `atm_root`、`are_root`、`gaia2_filesystem`、`scenario_root` 和新工作区内的 `sandbox_root`。路径样例和固定源码说明见 [上游文档](docs/upstream_v0_6.md)。密钥只放环境变量，不写进配置、日志或 Git。

## 四组探索性实验

| 单元 | 方法 | 攻击 |
|---|---|---|
| B1 | modular_risk_aware | 无 |
| B0 | modular_task_only | 无 |
| A0 | modular_task_only | 已有单次邮件注入 |
| A1 | modular_risk_aware | 已有单次邮件注入 |

固定场景为 `scenario_universe_22_kb5ah9`。两种方法共享检测器、工具权限、视图能力和预算，只在选择规则上不同。每组估算上限 0.4 USD，合计 1.6 USD，按 B1→B0→A0→A1 顺序；失败请求与未知费用预留也计入同一账本。

```powershell
# 只做四组 dry-run 并冻结配置，不调用模型
.\.venv\Scripts\python.exe scripts/run_v0_6_pilot.py prepare

# 只有明确确认费用后才运行；接线级故障会停止后续单元
.\.venv\Scripts\python.exe scripts/run_v0_6_pilot.py run --acknowledge-model-cost
```

脚本固定使用 `results/runs/v0_6_modular_pilot/20261015_01/`，拒绝覆盖已有清单或自动重复批次。原始模型输入、工具结果与轨迹只保存在本地，整个 `results/` 继续被 Git 忽略。

## 尚未证明的事情

- 没有独立通用的合法任务完成判据；官方 success 与任务完整完成分开记录。
- 普通摘要不是去污证明；字段投影只证明本次交付没有包含被排除字段。
- 重查是同一来源，更换 Worker 不保证未来结果安全。
- 没有真实并发、全环境回滚或经校准的未来 Token 预测。
- ATM 固定提交缺少根 LICENSE 文本，仅使用本地源码依赖；再分发材料缺口保持 OPEN。

## 历史记录

此前五个 Gaia2 官方 Single Agent 基线及其原始数字保留在 [2026-10-13 日志](docs/research-log/2026-10-13.md)。它们没有使用 v0.6，不能成为新方法的成绩。

v0.5 的架构、算法、命令手册和实施报告作为历史文档保留：[旧架构](docs/architecture.md)、[旧算法](docs/algorithm.md)、[旧 CLI](docs/cli.md)、[v0.5 报告](docs/reports/v0_5_implementation_report.md)。
