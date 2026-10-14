# v0.5 工程链路实施与 Review 报告

日期：2026-10-14，Asia/Shanghai。依据用户提供的 v0.5 方案说明和代码指令，代码指令为实施范围。

**最终工程状态：ENGINEERING_PATH_VALIDATED_OFFLINE。真实模型状态：REAL_DEEPSEEK_RUN_NOT_AUTHORIZED。** 本轮真实 DeepSeek/API 请求 **0**；没有运行收费命令、研究矩阵、攻击搜索或训练。离线验收不能证明实际性能或安全收益。

## 1. 版本与权限

- 开始：`v0.3-joint-controller`，HEAD `49db265d1e2dfff0d9bf88177de6008706ff04fd`，工作区干净，与任务书一致。检查了 status、HEAD 和最近五条 log，确认 v0.4 模块存在。
- 实施阶段结束：`refactor/v0.5-engineering`，HEAD 仍为同一提交；代码、配置和文档在工作区等待 Review。9 项 `git mv` 已写入索引，其余修改未提交。
- 实施阶段**没有 commit / push / merge / PR**，没有 reset / stash / clean，没有改 main。前一轮保存 v0.4 的授权不作为本轮自动发布指令。
- 后续保存：用户于 2026-10-14 明确要求“commit并推送”，因此将本轮改动保存为 Git 提交并推送 `refactor/v0.5-engineering` 同名远程分支。提交备注保留测试结果和 OPEN / BLOCKED；此次授权不包含新的付费实验。
- 参考了本机 MANTA 归档里的 main.py、cli/commands.py、cli/settings.py、cli/artifacts.py、scripts/README.md、pyproject.toml；只借鉴入口/产物职责划分，未复制它的算法或依赖栈。归档没有自己的 Git 元信息，不拿父仓库 HEAD 冒充其版本。

## 2. 唯一真实执行链

```text
security-mas / python -m security_adaptive_mas / scripts
  → cli.main → api.run
  → run_framework.main(argv, design_override=...) → run_case
  → 官方 ARE ScenarioRunner / Coordinator
    ↔ are_bridge 的实际通知、模型输入和工具边界
    ↔ Controller → joint_rewriter → GraphRuntime → FrameworkTeam
    ↔ 同一个 ToolGateway、DeepSeekClient、RunBudget
  → 原有 trace / topology_events / summary + 新 evaluation.json
  → report 只读汇总已有结果
```

CLI 没有另建 ScenarioRunner，没有用 subprocess 启动第二个运行器。experiment run 的方法和 seed 通过校验后的配置传入同一个 main；旧入口仍可直接使用。scripts 仅转发参数，不另实现选图、权限、费用或评分。

## 3. F1 / F2 / F3 / F4 实际修复

### F1：把格式合格与任务完成分开

角色输出仍使用原有字段、非空和证据 ID 检查（organization_contracts.py），没有增加 LLM Judge。实际绑定结果现在明确写 `artifact_schema_valid=true`、`semantic_verified=unknown`、`legitimate_task_completed=unknown`、`validation_status=UNVERIFIED`；GraphRuntime 拒绝 schema checker 直接声称语义或任务完成。

需求中的 satisfied 只说明结构性产物已经可读取，新增 `information_available`。planned 是具体获取计划，仍不能通过敏感操作的实际输入检查。字段合格的普通资料继续流转，ToolGateway 权限没有放宽。没有通用内容 checker，因此内容正确和合法任务完成仍 UNKNOWN。

覆盖：新增 `test_schema_valid_does_not_complete_task_and_planned_is_not_available`；原有 `test_planned_dependency_waits_for_actual_checked_output` 继续检查 planned 阶段敏感动作被拒绝、实际交付后可继续。

### F2：分开两种风险量

temporal_plan_risk 新增明确名称 `low_trust_potential_reachability` 和 `suspected_source_reachability`，保留旧名称为兼容别名。前者是正常外部数据也可能具有的结构潜在传播，后者只从已登记可疑对象出发。

选图日志说明采用后者、与 KEEP 比较的范围，以及没有报警不代表零风险。没有受保护目标时风险不可比，joint/fixed_security/事后过滤明确报 `UNDECIDABLE_risk_comparison_no_protected_targets`，不伪装成任务缺提供者。语义采纳与共享应用状态仍不可观察；没有训练新检测器。

覆盖：新增 `test_risk_names_separate_potential_and_suspected_sources`，证明普通低可信信息可以结构可达，但不能直接变成可疑攻击或成功。

### F3：去掉“交付更多、轮次更多就是更好”

select_rewrite_plan 不再使用 support / protocol_support 奖励。satisfied / planned / unknown 分别记录，不合格需求不能计为已完成。按操作可执行且授权、必要信息已可用或可执行计划、实际可用优先、可比风险、声明工作量和稳定 tie-break 排序。完全相同代价优先 KEEP。reactive_isolation 也不绕过必要信息约束。

13 个拓扑生成族、原有 compact 候选和实际角色执行保留，未新增复杂奖励或假成本系数。declared_work 继续只是步骤/字符代理；empirical 不在本轮训练。

首轮旧测试出现 **12 failed / 90 passed**：12 个拓扑夹具没有登记信息需求，却要求必定派工，依赖旧“探索”偏好。调整夹具，使每个生成族有沿实际图路由可提供的必要输出；链式夹具另明确前后依赖。全部 13 族的执行检查保留，不删除失败测试。compact 一轮可胜过两轮，产物数量断言相应按真实执行约定检查，不再用“更多产物”作为通过条件。

覆盖：新增 `test_selector_keeps_without_demand_and_chooses_cheaper_required_plan` 检查 KEEP、有效重组、缺提供者拒绝。另两个中间失败来自测试在修改需求后重复使用旧候选；改为与实际重规划一致地重新生成候选，未添加生产兜底。

### F4：诚实保存实际状态和费用

原 official_runner_success 不变，新增运行 evaluation.json、明确的验证/完成未知字段、实际 model_request_submitted 事件数和规范化 attack_target_achieved。旧邮件 scoring 的字符串 true/false 保留，报告仅做明确转换并附原始 scoring，不改旧文件。预算预检拒绝不算提交模型请求。

同时修正 design dry-run 原本显示旧策略默认值的问题，现在显示实际配置 method、模型和预算。设计哈希统一为实际生效配置的 canonical JSON 哈希（包含矩阵 method/seed），不是仅哈希基础 YAML；旧 design hash 的断点不能直接当作同配置恢复，新旧代码/配置冻结检查会明确拒绝。无 design 的旧基线执行策略不变。

## 4. 工程入口与安装

- pyproject 声明 `security-adaptive-mas==0.5.0`、Python >=3.10、console script、基础 YAML 与可选 model/ARE/data/dev 依赖。ARE 1.2.0 来自固定上游 pyproject，LiteLLM 1.71.1 来自本机现有元数据，数据依赖沿用已有 requirements；本轮只实测 Python 3.12.13。
- 当前 venv 原来缺 setuptools/wheel/pip。使用 **uv --offline** 从本机缓存安装 setuptools 84.0.0 / wheel 0.48.0，没有外网下载。editable 安装先经 uv 成功，又经本机已有 pip 的 `--python` / `--no-index` / `--no-build-isolation` / `--no-deps` 成功。
- Ruff 0.11.6 缓存不存在，离线安装明确失败；使用本机已缓存 Ruff **0.16.9**，同步 pyproject 与 pre-commit 版本，限定新增入口/scripts/v0.5 测试范围通过。没有历史全仓格式化。未运行完整 pre-commit hooks，不把 Ruff 通过写成全部 hook 通过。
- doctor 检查本机 Python、配置、模块/数据/源码路径、key 布尔值和阻塞状态，不自动下载、安装或导入 AgentDyn 全套框架。现场 deepdiff=false，ARE、MANTA 小内核与五个 JSON 路径存在。
- experiment plan 复用 make_matrix，默认 **140 条 NOT_RUN**，即 20 开发任务 × 7 方法。冻结 40 个 ID 和旧 manifest 未改；不是任务执行。
- experiment run 强制 limit、费用确认、总上限与单场预算；预先核对所选单位、配置/数据/代码快照，复用旧运行器账本作预留和结算。只支持明确的 Gaia2 方法/模型，自动矩阵目前 attack=none；未核验攻击显式 UNSUPPORTED。
- 单场已有邮件注入入口仍通过 run 转发到原 AttackOverlay/evaluator；没有改载荷或新增攻击家族。
- 新运行目录保留旧结构，加 evaluation.json。report 列出实际存在文件，不给历史目录回填新文件。

完整可复制命令见 [CLI 手册](../cli.md)、[scripts 用法](../../scripts/README.md)、[开发环境说明](../development.md)。

## 5. 实际执行的非付费检查

| 检查 | 实际结果 |
|---|---|
| `python -m compileall -q security_adaptive_mas scripts` | 通过 |
| `python -m pytest -q -p no:cacheprovider tests` 最终全量 | **109 passed，1 warning，0 skipped，36.47 秒** |
| 新 v0.5 聚焦测试单独执行 | 7 passed；已包含在 109 内，不能相加 |
| 与 CI 一致的可移植子集 | **4 passed，3 deselected，3.06 秒**；不是全量 CI 结果，也不隐藏 ARE 失败 |
| editable 安装、console / module help | 通过；离线方式见下方命令 |
| doctor | 通过，只输出 api_key_present 布尔值，实际模型请求 0 |
| 新旧 run dry-run | 12 个核心字段一致：任务、配置/哈希、方法、风险选择、模型、模式、四个单场限额、批次限额 |
| scripts help 与三个实际脚本入口 | dry-run、140 条计划、历史只读汇总均通过 |
| 参数/费用拒绝与总账本预留不足 | 通过，runner 未调用，未请求模型 |
| 真实 ARE 只读工具与邮件注入接口 | 全量测试中的现有真实 ARE 测试通过，详见下文 |
| 限定范围 Ruff 0.16.9 | All checks passed |
| Git working / cached diff --check | 通过，LF→CRLF 提示是本机行尾设置，不是测试警告 |

唯一 pytest warning 是上游 Pydantic class-based config 弃用提示。测试没有使用真实 API；fixture 的模拟完成只用于明确标注的 unit test，不作为付费执行证据。

### 真实 ARE 集成究竟测了什么

复用了 `tests/test_v0_2_full_loop.py::test_verified_are_email_field_overlay_and_sandbox_tool_scope`：官方导入 `22_kb5ah9`、读取实际 INBOX 中已核验邮件、经 GuardedARETool/ToolGateway 返回 typed 邮件结果，受控附加内容仅出现在返回副本，应用原邮件正文不变；最终 INBOX/TRASH 独立检查显示目标未删除。没有真的执行删除。

另外 `tests/test_v0_4.py::test_real_are_gateway_requery_keeps_external_source_label` 使用真实 ARE 读取并检查重查来源。两项都未调用真实 DeepSeek。离线 exposure 日志/模拟提交回调不能声称真实模型已接触载荷或抵抗了攻击。

### 读取的真实历史结果

实际输入：`results/v0_3_pilot/20261014T052653Z_365350/scenario_universe_22_kb5ah9/9599ecd278d8/summary.json`。

汇总保留：26 次请求、621393 输入 Token、19971 输出 Token、费用估算 0.21038309999999996 USD、6 次有效图补丁、official_runner_success=true、budget_exhausted；attack_delivered=true、目标未达成，legitimate_task_completed=unknown。**这是旧 v0.3 的数据，不是本轮新运行。** 原 summary SHA-256 与报告保存值一致，源文件未改。旧结果没有 evaluation.json，报告如实列 absent。

### 实际命令及本机产物

```powershell
uv pip install --offline --python .venv/Scripts/python.exe setuptools wheel
uv pip install --offline --no-deps --no-build-isolation --python .venv/Scripts/python.exe -e .
python -m pip --python .venv/Scripts/python.exe install --no-index --no-build-isolation --no-deps -e .
.\.venv\Scripts\security-mas.exe --help
.\.venv\Scripts\python.exe -m security_adaptive_mas --help
.\.venv\Scripts\security-mas.exe doctor --config configs/research_v0_4.yaml
.\.venv\Scripts\python.exe -m compileall -q security_adaptive_mas scripts
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests
uvx --offline ruff check security_adaptive_mas/api.py security_adaptive_mas/__main__.py security_adaptive_mas/cli scripts tests/test_v0_5.py
.\.venv\Scripts\python.exe scripts/check_docs.py
```

本机忽略目录 `results/v0_5_validation/` 保存 doctor.json、dry_run_old.json、dry_run_new.json、dry_run_comparison.json、development.json、script_development.json、script_dry_run.json、historical_pilot_report.json、script_historical_report.json。全量测试输出为 `work/test_logs/v0_5_full_suite.txt`。这些不是新的真实模型运行，未加入 Git。

CI 新增 Windows 离线工程检查：安装依赖阶段可从包源准备环境，检查阶段没有模型/数据下载。只运行可移植 CLI/config/计划/费用拒绝/report 子集，不假设完整 ARE/MANTA checkout 或全量数据。**GitHub Actions 尚未触发**，只能报告本机相同子集已通过。

## 6. Documentation Migration

开工时全部 23 份 Markdown 和旧→新映射已保存在 [归档索引](../archive/README.md)。本轮新增 8 份，完成后共 31 份；没有删除历史 Markdown。

| 分类 | 数量 | 准确路径 |
|---|---:|---|
| 原位置保留 | 14 | 根 README；data/gaia2/README；docs/archive/D_MIGRATION；下列 5 份原位报告和 6 份日志/索引 |
| 原位更新 | 5（包含在 14 内） | README.md；docs/archive/D_MIGRATION.md；docs/research-log/2026-10-12.md、2026-10-13.md、2026-10-14.md。除 README 更新外仅调整相对链接 |
| 新建 | 8 | docs/architecture.md、algorithm.md、cli.md、experiments.md、development.md、archive/README.md、reports/v0_5_implementation_report.md；scripts/README.md |
| 移动归档 | 9 | 5 份 design、3 份 implementation、1 份 handoff；完整映射见归档索引 |
| 未处理 / 无法迁移 | 0 | 未发现程序用固定路径读取这些文档；原有研究开放问题按历史原文保留 |

5 份原位报告：framework_v0_1_five_case_report.md、framework_v0_2_paid_22_kb5ah9_2026-10-14.md、gaia2_screening_report.md、v0_3_attacked_single_comparison_22_kb5ah9_2026-10-14.md、v0_4_implementation_report.md。

6 份日志/索引：docs/research-log 的 2026-10-11.md、2026-10-12.md、2026-10-13.md、2026-10-14.md、README.md、TEMPLATE.md。历史正文及数字与 HEAD 比较，仅允许链接目标不同；原有付费报告和 v0.4 报告没有内容改写。

迁移使用 git mv。git diff HEAD 的 name-status 能识别 9 个 R，没有历史文件被无解释删除。`scripts/check_docs.py` 检查全部受跟踪及新增非忽略 Markdown 的相对文件/图片目标，并检查目标可随 Git 发布；最终实际检查 **213 个文件/图片目标，0 断链**。页内/复杂标题锚点和外部 URL 没有逐一自动验证，这一范围明确保留。

## 7. OPEN / BLOCKED / UNSUPPORTED

### V05-01 [BLOCKED] AgentDyn

- 位置：doctor / 现有 AgentDyn 归档。
- 已确认事实：本机 deepdiff 不存在；v0.4 记录过真实 suite 导入因此失败，本轮没有重新安装或修复它。
- 无法确定什么：实际工具副作用目录、能力映射与完整 adapter。
- 影响：不能执行 AgentDyn 单位；不影响 Gaia2 和本轮只读工具检查。
- 已经完成：doctor 明确报告缺依赖和适配阻塞。
- 最小下一步：单独审阅依赖安装并核验拟用工具的副作用，再接入 adapter。

### V05-02 [OPEN] 独立内容与合法完成

- 位置：RoleContract / bind_output / summary / report。
- 已确认事实：当前检查器只核字段和证据 ID，官方 success 单列。
- 无法确定什么：通用内容正确性和整个用户任务真实完成。
- 影响：semantic_verified、legitimate_task_completed 仍 unknown，不能计算可信安全完成收益。
- 已经完成：状态区分、敏感动作等待实际输入和普通资料流转。
- 最小下一步：选定具体任务，提供独立且可执行的状态/内容检查器。

### V05-03 [BLOCKED] 实证成本 / 并发 / 完整恢复

- 位置：cost_model / ARE 调度与恢复边界。
- 已确认事实：缺充分真实 v0.4/v0.5 开发校准数据；并发通知原子语义和完整恢复协议未建立。
- 无法确定什么：预测收益、真实并行加速、同状态分叉的可比性。
- 影响：本轮继续 declared_work 与 sequential，不训练或宣称性能收益。
- 已经完成：记录实际用量的共用接口、unknown 状态和明确不支持条件。
- 最小下一步：先定义可信完成判据和有限开发验证，再考虑校准及单独恢复/并发设计。

### V05-04 [NOT_IN_SCOPE / NOT_RUN] 真实攻击与付费闭环

- 位置：统一 run / experiment run / attack 协议。
- 已确认事实：已核验邮件入口可复用，其他家族没有完整实投递与独立后果判据；本轮不授权真实模型请求。
- 无法确定什么：真实 v0.5 模型行为、四家族 ASR 或安全收益。
- 影响：自动矩阵只支持无攻击单位；固定邮件单场入口仅离线检查。
- 已经完成：真实 ARE 返回字段/邮箱状态回归和参数/预算拒绝测试。
- 最小下一步：Review 后明确授权一场有限模型验证；其他家族逐例核验，不能改标签凑数。

### V05-05 [OPEN / UNSUPPORTED] 可移植安装与同目录并发批次

- 位置：pyproject / config.ROOT / code_state / 账本。
- 已确认事实：editable 可用；基础 wheel 不包含 configs、数据和上游源码，完整运行还依赖 checkout 与 Git。账本沿用单进程顺序操作。
- 无法确定什么：从空目录完整环境兼容性、独立 wheel 全链复现及多进程原子费用预留。
- 影响：不能把安装 CLI 等同真实 ARE 就绪，不支持多个进程共享输出账本。
- 已经完成：doctor、显式 SECURITY_MAS_ROOT、顺序批次和文档边界。
- 最小下一步：独立环境验证；若确需并发批次，另行定义原子账本机制，本轮不加隐藏重试/锁层。

### V05-06 [OPEN] 上游输入归因与资源边界

- 位置：官方 Coordinator 渲染 / 固定角色与 GraphRuntime 限额。
- 已确认事实：v0.4 的未知输入对应关系、固定组织新角色限制、最多 7 个待处理角色及 12 次有效补丁约束保留。
- 无法确定什么：完整逐对象归因、任意长任务是否能在这些资源界限内完成。
- 影响：正式实验必须记录停止条件，不能暗中放大上限或截掉任务。
- 已经完成：保持原有显式报错和历史说明。
- 最小下一步：逐项 Review 稳定输入标识和任务规模/预算配置，之后才作实验决策。

## 8. Review 结论与下一步

| 状态 | 范围 |
|---|---|
| DONE | 可安装入口、复用唯一运行器、F1/F2/F3 局部修复、诚实结果字段、scripts、历史只读汇总、文档归档和本机非付费验收 |
| PARTIAL | CI 已配置且同子集本机通过，但远程未运行；doctor 是只读存在性检查，不证明完整依赖可运行 |
| BLOCKED / OPEN | AgentDyn、独立语义/合法完成、实证校准、并发、完整恢复、部分输入归因 |
| NOT_IN_SCOPE / NOT_RUN | 收费实验、大矩阵、四家族扩展、RL/GNN、训练、大搬核心目录、研究收益结论 |

请先 Review 状态语义、无需求时 KEEP、计划/实际交付边界、费用预留和文档边界。之后再决定是否提交/推送及是否授权有限真实模型运行；本轮到此停止。

## 9. 本轮实际文件清单

对 HEAD 比较：**15 个已有文件更新、9 个文档移动、25 个新文件**。移动的旧/新路径详见第 6 节与归档索引；以下列出更新和新建的完整路径。results/work 内的验收产物为本机忽略文件，不在提交范围。

```text
更新（15）：
.gitignore
README.md
docs/archive/D_MIGRATION.md
docs/research-log/2026-10-12.md
docs/research-log/2026-10-13.md
docs/research-log/2026-10-14.md
security_adaptive_mas/config.py
security_adaptive_mas/dataset_store.py
security_adaptive_mas/dynamic_team.py
security_adaptive_mas/graph_evaluation.py
security_adaptive_mas/graph_runtime.py
security_adaptive_mas/joint_rewriter.py
security_adaptive_mas/organization_contracts.py
security_adaptive_mas/run_framework.py
tests/test_v0_4.py

新建（25）：
.env.example
.github/workflows/ci.yml
.pre-commit-config.yaml
pyproject.toml
security_adaptive_mas/__main__.py
security_adaptive_mas/api.py
security_adaptive_mas/cli/__init__.py
security_adaptive_mas/cli/main.py
security_adaptive_mas/cli/doctor.py
security_adaptive_mas/cli/run.py
security_adaptive_mas/cli/experiment.py
security_adaptive_mas/cli/report.py
scripts/README.md
scripts/check_docs.py
scripts/run_dev.py
scripts/run_matrix.py
scripts/summarize_results.py
tests/test_v0_5.py
docs/architecture.md
docs/algorithm.md
docs/cli.md
docs/experiments.md
docs/development.md
docs/archive/README.md
docs/reports/v0_5_implementation_report.md
```
