# 2026-10-15 · v0.6：Issue #3 修复与四组真实实验启动检查

状态：**Issue #3 最小修复已提交、免费测试通过；真实实验 BLOCKED_RUNNER_MANIFEST_REUSE。**

本轮计划修复 JSON mode 契约后直接运行 B1→B0→A0→A1。已有付费授权有效；当前阻塞来自原 Runner 的重复运行保护。本轮没有新增模型请求，不能提供新的四组实验成绩。

## 1. Issue #3 的修复与免费测试

附件 `issue3_minimal_fix.zip` 的旧提示词内容与当前源码唯一匹配，但原补丁定位信息不适配：`git apply --check` 和 `--recount --check` 均失败。经逐字核对旧文本后，在本地生成正确上下文补丁；修改内容与附件完全一致，重新 check 后应用，没有改写补丁要求。

- 风险提示现在明确要求一个合法 JSON object，保留原来的三个字段、三个 suspicion 值和仅引用已给证据的规则。
- 提示词版本改为 `semantic-risk-v0.6-json-1`。
- `run_framework.py` 的运行元数据改为读取 `RISK_PROMPT_VERSION` 常量，避免新运行仍记录旧版本；这是版本记录同步，不改变执行逻辑。
- 将附件回归测试原样加入 `tests/test_risk_json_contract.py`。
- 风险解析 Schema、模型调用预算、重试、超时及组织决策均未改。MANTA、ATM、AOrchestra、GraphRuntime、策略和 AttackOverlay 未改。

现有 v0.6 环境实际执行 **16 passed / 3 warnings / 28.40 秒**：1 项附件测试、13 项 v0.6 测试、2 项适用的 DeepSeek 测试；警告来自 Pydantic 上游弃用项。首次沙箱测试因 pytest 临时目录权限失败，授权目录权限下重跑通过；没有为通过测试修改应用逻辑。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_risk_json_contract.py tests/test_v0_6_modular.py tests/test_deepseek.py::test_deepseek_fixed_route_history_usage_and_tool_parse tests/test_deepseek.py::test_deepseek_rejects_missing_key_and_bad_tool_json -q --tb=short --basetemp work/pytest_issue3_verified_20261015
```

本测试未调用真实 API。JSON 请求格式已获离线回归覆盖，修复后的真实 API 行为尚未验证。[原 Issue #3](https://github.com/ruihanzhu5-cyber/SecurityAdaptiveMAS/issues/3) 保留原始 HTTP 400 证据。

## 2. 冻结代码、配置及第三方版本

先提交修复，再在干净工作区冻结以下候选版本。它没有成功进入新 Episode；不能将其写成四组实际运行版本。

| 项目 | 冻结记录 |
|---|---|
| 分支 | `research/v0.6-modular-runtime` |
| 修复 Commit SHA | `0a211d875f210194407abce31d9facd18875f6e9` |
| 源码快照 SHA-256 | `e5bf806b46b4403cd0ee73d7100cc956b0d60dcaa3c2b19594aaf87754f56fb0` |
| 设计文件 SHA-256 | `0728891e8cf8b9f71c3884e0819985175a5c5dd2ae93eabe7d41a74eb7e350ec` |
| risk-aware 有效设计哈希 | `fed53399f6ec166e49a764e021374d76c23ef1bbef8b8ef60ea31dbfa91fa676` |
| task-only 有效设计哈希 | `f874918c62ee141f37f9c54cefa731b06015604e76107b015b6d37b4b09f20ca` |
| MANTA | `3da40879d690a25459cc401292e5c6f0cc366d84` |
| AOrchestra | `14a1a2051d6b03c479b706f8f555a60b8419e3b5` |
| ATM | `951e9aa8c8421e8b8d59302ee4727cc855f84714` |
| ARE | `a9fd2edccb4bc7e22fbf45c8482a5fef30a2a5a4` |

设计入口为 [research_v0_6.yaml](../../configs/research_v0_6.yaml)，固定来源见 [锁文件](../../configs/upstream_v0_6.lock.json)。冻结记录包含本机路径合并后的完整有效设计、源码文件哈希及副本，保存在本地 `D:/SecurityAdaptiveMAS-v0.6/work/issue3_20261015_verification/`。报告提交发生在冻结检查之后，不替代上述修复提交。

## 3. 原 Runner 的实际启动检查

使用现有脚本和费用确认参数，没有编写另一套 Runner：

```powershell
.\.venv\Scripts\python.exe scripts/run_v0_6_pilot.py run --acknowledge-model-cost
```

实际退出码为 1：

```text
RuntimeError: This pilot cannot be repeated automatically
```

原因已由源码和原清单核实：

1. 脚本固定 `BASE=results/runs/v0_6_modular_pilot/20261015_01`，只提供 prepare/run 和费用确认参数，没有新批次或输出目录参数。
2. 该路径已有上次 `BLOCKED_STOPPED_AFTER_B1` 清单，B1 是 FAILED。execute 只接受 `PREPARED_NO_PAID_REQUESTS`，因此在循环和模型调用前拒绝。
3. prepare 也明确禁止已有清单，不可用它覆盖上次记录。即使清空状态，旧快照也与本次修复不同，不能冒充同一实验。

按用户“脚本条件不满足记录 BLOCKED、不改参数”以及“本阶段只修 Issue #3”的要求，未修改脚本、重置清单、替换模块常量、另开账本或复制旧结果充当新结果。

## 4. 四组真实执行状态

计划统一场景 `scenario_universe_22_kb5ah9`、模型 DeepSeek Flash，费用 0.40 USD/Episode、累计 1.60 USD，单组 100 次请求、800000 Token、20 ARE turns；均保持原设定。

| 组别 | 方法 | 攻击 | 本轮状态 | 新 Run ID |
|---|---|---|---|---|
| B1 | modular_risk_aware | none | NOT_RUN：启动清单阻塞 | — |
| B0 | modular_task_only | none | NOT_RUN：启动清单阻塞 | — |
| A0 | modular_task_only | single_injection | NOT_RUN：启动清单阻塞 | — |
| A1 | modular_risk_aware | single_injection | NOT_RUN：启动清单阻塞 | — |

未创建新 Episode，因此各组没有新的官方评价、停止结果、合法任务状态或运行时长；均为 NOT_RUN，不能沿用上次 B1 的结果。

## 5. Agent、图、交付与攻击数据

本轮没有新 Agent 执行数据；创建/退休、有向边、ACL、WorkItem、MANTA Packet、ATM 契约和候选选择均为 NOT_RUN，不能把“未运行”填成一个真实单 Agent 图。

A0/A1 未启动，未产生攻击注入。攻击接触、风险判断、下游采纳、目标工具调用、网关阻断及最终环境状态均为 NOT_RUN；不能声明防御成功。若后续真实执行没有符合攻击入口条件的 Worker，应记录 `attack_delivered=false`，而不是验证了攻击防御。

## 6. 请求、Token、费用与时间

本轮 Runner 启动检查在任何请求之前终止，新增请求总数 **0**。各组实际 Token、模型时间、Worker/RiskMonitor/重组成本均为 NOT_RUN；没有 API 用量可供比较，启动检查耗时不能当成 Episode wall time。

原共享账本 `results/runs/v0_6_modular_pilot/20261015_01/v0_1_cost_ledger.jsonl` 仍计入历史运行 **0.020724 USD**，含已知费用 0.002451 USD 和未知上界约 0.018273 USD。这是上次失败 B1 的账本数，不是本轮新增收费。没有重置、转移或新开费用渠道；原 285 个证据文件在检查前后哈希和长度均匹配。

## 7. 新发现的工程阻塞与最小后续范围

**BLOCKED-RUNNER-MANIFEST-REUSE：**原脚本只能执行固定的一次清单，不能在保留历史目录和同一共享账本的条件下，启动新的修复后四组。

下一轮最小可选修改是：让同一个脚本支持一个新的唯一实验清单/快照目录，同时仍明确使用已有共享账本和累计上限，保留拒绝覆盖和代码冻结检查。不改变模型参数、攻击、算法或预算数值。此项属于 Runner 的生命周期调整，超出本轮仅修 Issue #3 的代码范围，因此没有自行实施。

其余 v0.6 OPEN 问题保持原样，不提前解决。

## 8. 能够支持与不能支持的结论

能够确认：附件 JSON 契约修复已应用并提交，相关 16 项免费测试通过，提示词版本记录同步，历史失败证据与账本保留，原 Runner 确实在新模型调用前拒绝重复清单。

不能确认：修复后的真实 DeepSeek 风险响应、四组真实闭环、主动委托、动态通信、风险驱动重组、任务收益、安全收益或端到端成本差异。本轮没有获得新的真实运行轨迹，不以离线 fixture 或上次失败 B1 填补。

历史失败单独保留在 [原 B1 HTTP 400 报告](2026-10-15_v0_6_B1_json_mode_http_400.md)。本报告记录后续最小修复与启动阻塞，两轮证据不混合。
