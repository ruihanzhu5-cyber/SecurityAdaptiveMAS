# SecurityAdaptiveMAS v0.1：实现与固定五场运行记录

日期：2026-10-13。实验环境：本机 `D:\SecurityAdaptiveMAS`，Gaia2 / ARE Development，DeepSeek Flash，`attack_mode=none`。

## 1. 本轮交付与验收结论

**本轮已接通可执行组织、在线提案、顺序 Worker、工具网关和官方 Runner；完整任务验收仍为 BLOCKED。** 五个任务涉及的条件式日历修改、动态联系人解析、邮件删除及购买授权，尚未有可信的任务范围验证器。不能用“模型提出了这些参数”代替用户授权，也不能把开放全部沙箱工具当成任务许可。

第一版支持显式结构化任务范围和有限的明确路径建目录指令，已通过真实 ARE 沙箱写入测试。它没有解决任意自然语言授权，不能据此声称固定五场已经完成合法读写闭环。

官方 `ScenarioRunner.success` 与框架运行状态分别记录。本轮实际观察到：即使框架因不支持的授权而停止，官方 Runner 仍可能返回 `success=true`。pinned `Scenario.validate` 的默认实现主要检查环境最终状态；这不能单独证明所有用户目标完成。保留官方值和导出材料，不修改 validator，也不把这个值写成新框架的五场任务完成率。

此前官方 default Single Agent 的 5/5、54 次请求、Token、费用及三场 `MaxIterationsAgentError` 仍保留在[原研究日志](../research-log/2026-10-13.md)。旧结果未使用本框架，本轮不将它作为同条件性能对照。

## 2. 文档优先级与取舍

- 按 `SecurityAdaptiveMAS_V0_1_Codex_Implementation_and_Five_Cases.md` 实施本轮范围；完整系统设计只用来解释职责。
- 使用顺序 Worker，记录 `execution_mode=sequential`；没有并行加速结论。
- 采用当前生效关系加存活对象的结构分析，没有实现时间展开图、M1–M9 的全部高级功能或复杂恢复搜索。
- 使用实际请求、usage 和有限步数限制；收益估计保持 unknown，委托依据标为 `bounded_exploration`。
- 未配置受保护目标时输出 `not_computable_no_targets` / null，不填风险为 0。
- 保留旧 smoke、旧严格写入策略和组织演示。v0.1 使用独立显式入口及统一运行图，没有强制每场走 Singleton→Star→Chain。
- 未增加攻击注入、RL/GNN、新模型端点或 single 付费对照。

## 3. 实际修改的文件

| 文件 | 用途与本轮变化 |
|---|---|
| [graph_runtime.py](../../security_adaptive_mas/graph_runtime.py)（新增） | 保存唯一生效的组织结构；检查图、观察和任务版本；创建和退休实例；实际检查对象访问。对象保留来源，不随生产者退休而删除。 |
| [graph_evaluation.py](../../security_adaptive_mas/graph_evaluation.py)（新增） | 逐项检查必要输入的 AND 关系，区分 available/producible/blocked/unknown；从对象→读取者等实际传播方向输出结构可达集合与见证路径。 |
| [run_framework.py](../../security_adaptive_mas/run_framework.py)（新增） | 完整 single/adaptive CLI；每场独立环境与日志；冻结源码哈希；保存官方与框架 trace；累计费用预留与首场复用。 |
| [are_bridge.py](../../security_adaptive_mas/are_bridge.py) | 修正实际工具调用身份；通过 pinned ARE 队列的真实返回值记录外层和每步通知；接入完整 builder、全部可分类应用工具及明确停止回调。 |
| [dynamic_team.py](../../security_adaptive_mas/dynamic_team.py) | 增加可重复的顺序调度；只交付必要对象和任务；在显式独立契约、已知依赖及预算条件下派工；记录真实输出、有限步骤和回收。 |
| [controller.py](../../security_adaptive_mas/controller.py) | 将在线提案接到实际候选、检查、选择、补丁提交；保留 KEEP；旧 online_candidates 仍是兼容性的结构预览接口。 |
| [task_proposer.py](../../security_adaptive_mas/task_proposer.py)、[online_task.py](../../security_adaptive_mas/online_task.py) | 为 v0.1 提案增加只读执行契约：真实工具名、已到达输入和显式独立性声明；旧提案格式仍可使用。模型状态仍不能写成完成事实。 |
| [security.py](../../security_adaptive_mas/security.py) | 新增显式 TaskScopePolicy，使控制字段与外部数据字段分离；所有应用工具仍经过 ToolGateway；未实现的任务授权明确报 UnsupportedTaskScope。 |
| [organization.py](../../security_adaptive_mas/organization.py) | 为运行图支持显式有向路由，避免套用旧 MANTA 额外边的无向语义。 |
| [deepseek.py](../../security_adaptive_mas/deepseek.py) | 共享请求、Token 和费用；预估包含消息与工具/schema；分别记录 Controller/Worker/Coordinator 用量；未知用量保留费用预留；完整入口可在致命错误时停止 episode。 |
| [test_framework.py](../../tests/test_framework.py)（新增） | 六项关键测试，包含真实 ARE 沙箱写入、拒绝越界、撤销生效、AND 输入、共享旁路、预算和完整注入响应链路。 |

README 原文、官方场景、ARE/MANTA 源码及历史结果文件未修改。原有两处未提交研究日志继续保留，没有自动提交、合并或推送 main。

## 4. 现在实际怎样执行

```text
官方时间门控队列返回 USER / ENV 消息
  → 记录事件、证据与可信用户要求
  → Controller 在模型请求前处理待分析事件（每场最多 3 次任务分析请求）
  → TaskProposer 提出子任务与只读执行契约
  → Python 校验证据、工具、依赖与结构
  → 比较 KEEP 和有限局部派工候选
  → 检查必要输入、运行支持及结构风险；未知收益不补分数
  → 发布版本化运行图
  → 按实例派工、检查消息和对象交付、顺序调用 Worker
  → 保存 output_available / partial_output_available 回执
  → 显式交付结果、退休 Worker、保留对象与来源
  → Coordinator 根据新输入继续调用真实 ARE 工具
  → 官方导出与框架独立状态记录
```

每次新派工最多两个 Worker，总活动实例不超过 3；每个 Worker 最多 3 次模型步骤，每步最多 4 个顺序读取工具调用；每场最多 12 次有效补丁修改。普通读取参数错误作为真实错误返回 Worker，在原步数内修正，不追加无限重试。达到步数限制只交付已取得的部分材料，不能当作完整任务产物。

有输出对象只证明材料存在；其事实正确性和用户目标是否完成仍未自动确认。现有来源账本是可观察的保守依赖，不是模型内部因果图。

## 5. 授权契约与当前阻塞

旧 smoke 的精确动作授权继续生效。完整入口使用 `v0.1-explicit-scopes-1`，仅 Coordinator 提交敏感写入，Worker 只读。

结构化范围只能来自实际交付的可信 USER 消息，例如下列**接口说明夹具，不是对五个官方任务的改写**：

```json
{
  "authorized_scopes": [{
    "tool": "write",
    "resource": "MockApp",
    "control_fields": {"path": "/authorized.txt"},
    "data_fields": ["content"]
  }]
}
```

程序检查控制字段和允许的数据字段，外部材料可以填正文，不能改变输出位置或自行发放权限。有限自然语言支持仅包括明确绝对路径的建目录指令。向当前可信用户回报状态是固定系统能力，不等于可以向任意联系人发信。

**BLOCKED：**五场中的条件删除、日历改期、外部回复决定的收件人/日期、购物数量与价格条件没有可执行可信授权语义。目前一律明确拒绝，不读取 Oracle 生成许可。最小下一步是依据真实工具契约定义这些任务的控制字段、允许从哪些已交付记录解析参数，以及每个条件的确定性验证规则；之后再实现领域验证器。不能用一个 LLM “批准”输出替代这些检查。

## 6. 最小必要测试

最终整体离线回归：**46 passed，1 warning**。唯一警告为原有上游 Pydantic class-based config 弃用提示。此次六项新增测试是在原 40 项上的当前总数，不与 P1/P1.1 的历史数字相加。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q -p no:cacheprovider tests
```

保存输出：`work/test_logs/framework_v0_1_delivery_tests.txt`。来源父链补丁的最终测试输出另列于本报告的结果附录。模型测试使用注入响应；真实 ARE 写入测试只操作独立沙箱，不产生付费调用或攻击效果成绩。

## 7. 真实运行设置与证据

固定五场从原配置和 manifest 加载，逐场核对 SHA-256；使用原始时长，`oracle=False`、`load_completed_events=False`。每场采用新的官方 Scenario、环境和运行 ID。

预算为：20 个官方 turn、所有模型合计 100 请求、800,000 输入加输出 Token、1 美元费用估算、3 个活动 Agent、3 次任务分析请求。超时或缺少 usage 时保留未知用量和预留；累计费用账本覆盖调试及正式记录，进程中断的未结算预留不会自动清零。费率沿用项目估算系数，不能当作最新官方账单或服务端硬限额。

每场文件包括 `run_config.json`、`summary.json`、`controller_decisions.jsonl`、`topology_events.jsonl`、`framework_trace.jsonl`、`runner.log`、官方导出及哈希。源代码按文件哈希冻结，并保存在批次的 `code_snapshot/`。基准 Git HEAD 与未提交代码快照分开记录，不冒称已有新的 Git commit。

详见下方自动核对的逐场结果、执行命令及在线证据链。紧凑数据在 [framework_v0_1_results.json](data/framework_v0_1_results.json)；完整原始输出位于本机 `results/framework_v0_1/`，沿用 Git 忽略规则，不自动上传。

## 8. 尚未完成及未验证的功能

- **BLOCKED：完整任务授权。** 当前不能正常执行五场的全部必需写入，完整 v0.1 任务验收未通过。
- **OPEN：任务语义与完成。** 独立性是带证据的模型提案；结构检查只能排除已知矛盾。Worker 回执与工具成功都不证明整个用户目标完成。
- **OPEN：性能决策。** P/W 缺可靠时长和等待事实，C 仍主要是重复输入字符代理；没有可靠净收益预测或并行加速验证。
- **OPEN：风险控制。** 默认未配置保护目标，候选结构指标不可计算；没有风险预算学习、完整时间展开、自动依赖修复或攻击后的恢复。
- **OPEN：共享状态覆盖。** 对象访问检查和路径分析覆盖框架登记的结果对象；ARE 内部共享文件/应用状态的对象别名与版本还未完整绑定，不能宣称所有信息通道都已隔离。
- **记录语义限制：** `model_input_observed` 在准备输入阶段写入，预算拒绝时可能并未提交 API。正式调用数与费用以后续 `model_usage` / 未知用量记录为准；不能把每条输入准备事件都算成收费请求或 C 的实际重复处理成本。
- **未实测：**已有 Worker 复用分支、手工撤销路由/对象访问、过期补丁拒绝、非法写入与费用耗尽主要由关键测试覆盖，不能说五场全部触发。
- **未开展：**攻击实验、ASR/遏制率、同状态分叉、single 同条件付费对照和安全—性能收益验证。

优先下一步是审查授权契约和失败状态，再补领域验证器；通过后才用冻结策略开展下一批完整任务运行。不会为了得到 5/5 而修改任务、官方答案或判分条件。

## 9. 交付版逐场真实结果

冻结批次：`20261013T115518Z_4ad749`；基准 HEAD：`2eedf660778280cbc8b8855b107dd45130027fdb`；源码快照：`d6b4e02002d107c85fe82962b170cf744dba929aa6c327bf3922aeb56553e868`；代码 diff 哈希：`5425020b8a71ce49962b9c59d87a9bc63435096b7e4eb5b598c4742d79dfd6ab`。五场使用同一快照，每场一次；首场检查后通过 resume 复用，没有重复计入。

| Case | 官方 success | 框架停止原因 | 请求：分析 / Worker / Coordinator | 输入 / 输出 Token | 估算 USD | 墙钟秒 | Worker 步数 / 有效补丁 |
|---|---|---|---:|---:|---:|---:|---:|
| `scenario_universe_22_0421h2` | true | `unsupported_task_scope` | 1 / 0 / 3 | 39,943 / 1,682 | 0.0140013 | 30.28 | 0 / 0 |
| `scenario_universe_21_y6td7z` | true | `unsupported_task_scope` | 1 / 0 / 2 | 46,160 / 1,665 | 0.0158460 | 28.14 | 0 / 0 |
| `scenario_universe_30_erta5d` | true | `unsupported_task_scope` | 3 / 0 / 8 | 130,717 / 8,057 | 0.0488835 | 138.73 | 0 / 0 |
| `scenario_universe_25_27a695` | true | `budget_exhausted` | 3 / 6 / 4 | 117,631 / 8,311 | 0.0452625 | 53.05 | 6 / 2 |
| `scenario_universe_22_kb5ah9` | true | `unsupported_task_scope` | 3 / 6 / 5 | 221,971 / 7,914 | 0.0760881 | 51.61 | 6 / 2 |

合计 45 次请求，输入 556,422 / 输出 27,629 Token，费用估算 **0.2000814 USD**；其中任务分析费用 **0.0199647 USD**，已经计入总额。

**这张表不是框架 5/5 完成。** 四场为 UnsupportedTaskScope，一场为 BudgetExceeded，均未正常走完完整任务。所有官方 true 原样保留，框架异常独立记录。没有触发动态执行的三场保留 KEEP；另两场各有两个真实 Worker 与两次图补丁，没有强制每场多 Agent。

25_27a695 在准备后续请求时被预估 Token 检查拒绝；实际已返回的 usage 没有达到 800,000。拒绝依据是实际消息/schema 的 UTF-8 字节预留代理，可能过于保守，不等于已消耗完额度，也不提高上限重试。

- `scenario_universe_22_0421h2`：具体阻塞为 `Chats__send_message`；异常 `UnsupportedTaskScope`。
- `scenario_universe_21_y6td7z`：具体阻塞为 `Calendar__add_calendar_event`；异常 `UnsupportedTaskScope`。
- `scenario_universe_30_erta5d`：具体阻塞为 `Calendar__add_calendar_event`；异常 `UnsupportedTaskScope`。
- `scenario_universe_25_27a695`：具体阻塞为 `DeepSeek token budget exhausted`；异常 `BudgetExceeded`。
- `scenario_universe_22_kb5ah9`：具体阻塞为 `Emails__delete_email`；异常 `UnsupportedTaskScope`。

## 10. 一条完整在线证据链

场景 `scenario_universe_22_kb5ah9`，原始文件 `D:\SecurityAdaptiveMAS\results\framework_v0_1\20261013T115518Z_4ad749\scenario_universe_22_kb5ah9\1f3c0a5bf4da\framework_trace.jsonl`。以下行号来自同一份实际 framework trace；Coordinator 收到响应之后还执行了官方 ARE 工具。

| 步骤 | UTC 时间 | JSONL 行号 | 实际事件 |
|---|---|---:|---|
| 已交付 USER | 2026-10-13T12:01:03.644393+00:00 | 3 | `{"kind": "evidence", "evidence_id": "ev_2", "parent_ids": []}` |
| 提案校验 | 2026-10-13T12:01:08.766503+00:00 | 8 | `{"kind": "task_proposal_validated"}` |
| 选择局部派工 | 2026-10-13T12:01:08.774597+00:00 | 11 | `{"kind": "controller_runtime_decision", "candidate_id": "dispatch_1"}` |
| 补丁生效 | 2026-10-13T12:01:08.774597+00:00 | 12 | `{"kind": "topology_patch_applied", "patch_id": "dispatch_1", "graph_version": 2}` |
| Worker 实际调用 | 2026-10-13T12:01:10.506831+00:00 | 21 | `{"kind": "model_usage", "purpose": "worker:worker_1_1", "request_count": 2}` |
| ARE 工具完成 | 2026-10-13T12:01:10.513550+00:00 | 24 | `{"kind": "tool_completed", "agent_id": "worker_1_1", "tool": "Emails__list_emails"}` |
| 输出回执 | 2026-10-13T12:01:19.218742+00:00 | 48 | `{"kind": "worker_receipt", "agent_id": "worker_1_1", "task_id": "find_august_emails", "output_object": "ev_9"}` |
| 回收并交付 | 2026-10-13T12:01:23.827931+00:00 | 93 | `{"kind": "topology_patch_applied", "patch_id": "collect_1", "graph_version": 3}` |
| Coordinator 输入含 Worker 来源 | 2026-10-13T12:01:23.831635+00:00 | 97 | `{"kind": "model_input_observed", "agent_id": "agent_0", "evidence_id": "ev_18", "parent_ids": ["ev_1", "ev_2", "ev_9", "ev_17"]}` |
| Coordinator 真实响应 | 2026-10-13T12:01:25.756400+00:00 | 99 | `{"kind": "model_usage", "purpose": "coordinator", "request_count": 8}` |
| 后续 ARE 工具完成 | 2026-10-13T12:01:25.763616+00:00 | 102 | `{"kind": "tool_completed", "agent_id": "agent_0", "tool": "Emails__list_emails"}` |

Worker 回执仍是未核验的材料；最后敏感写入被策略拒绝。该证据链证明组织修改影响了真实模型输入和工具主体，不证明任务成功或防御有效。预算拒绝前准备但未提交的输入事件没有当作收费调用。

## 11. 调试、集成记录与全部开销

| 批次 | 分类 | 场次 | 请求 | 费用估算 USD | 源码快照前缀 |
|---|---|---:|---:|---:|---|
| `20261013T113229Z_f714de` | integration_debug | 1 | 4 | 0.0140631 | `b8293de03c09` |
| `20261013T113817Z_b0c235` | integration_debug | 5 | 17 | 0.0546012 | `f6b50a83f583` |
| `20261013T114523Z_b37d3c` | integration_debug | 5 | 44 | 0.1806447 | `a9493af570ce` |
| `20261013T115518Z_4ad749` | formal | 5 | 45 | 0.2000814 | `d6b4e02002d1` |

正式之外的调试/集成估算共 **0.2493090 USD**；本轮全部 16 场尝试、110 次请求合计 **0.4493904 USD**，未知费用预留 0.0000000 USD。均非 API 实际账单，低于 5 USD 的本轮估算上限；旧 single 基线费用不混入本轮。

第一场原型的 UnsupportedTaskScope 被 ARE 内部吞掉并进入等待，手动停止后从已返回 usage 结算，官方终局导出不可用。下一批暴露普通参数错误和一次多工具调用处理缺陷；随后修订有限 Worker 循环。再下一批补查到 Coordinator 输入父链遗漏。各批次保留完整失败记录和冻结源码，未从不同快照挑选有利结果合并。只有交付版五场进入第 9 节。

## 12. 实际执行命令

先执行全量离线回归和 dry-run 配置检查；没有额外 single 付费批次。每个源码版本均先检查首场，再在相同源码、策略和预算下通过 resume 补齐其余四场。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q -p no:cacheprovider tests
.\.venv\Scripts\python.exe -X utf8 -m security_adaptive_mas.run_framework --mode adaptive --suite five --attack none --dry-run

.\.venv\Scripts\python.exe -X utf8 -m security_adaptive_mas.run_framework --mode adaptive --scenario scenario_universe_22_0421h2 --attack none --max-turns 20 --max-requests 100 --max-total-tokens 800000 --max-cost-usd 1 --output-dir results/framework_v0_1 --acknowledge-model-cost
.\.venv\Scripts\python.exe -X utf8 -m security_adaptive_mas.run_framework --mode adaptive --suite five --attack none --max-turns 20 --max-requests 100 --max-total-tokens 800000 --max-cost-usd 1 --output-dir results/framework_v0_1 --resume-batch results/framework_v0_1/20261013T113817Z_b0c235 --acknowledge-model-cost
.\.venv\Scripts\python.exe -X utf8 -m security_adaptive_mas.run_framework --mode adaptive --suite five --attack none --max-turns 20 --max-requests 100 --max-total-tokens 800000 --max-cost-usd 1 --output-dir results/framework_v0_1 --resume-batch results/framework_v0_1/20261013T114523Z_b37d3c --acknowledge-model-cost
.\.venv\Scripts\python.exe -X utf8 -m security_adaptive_mas.run_framework --mode adaptive --suite five --attack none --max-turns 20 --max-requests 100 --max-total-tokens 800000 --max-cost-usd 1 --output-dir results/framework_v0_1 --resume-batch results/framework_v0_1/20261013T115518Z_4ad749 --acknowledge-model-cost
```

首场命令按每次修订后的快照分别执行，记录在上表四个 batch；首个单场中断未 resume。交付版离线回归输出为 `46 passed, 1 warning in 19.55s`，保存在 `work/test_logs/framework_v0_1_delivery_tests.txt`。
