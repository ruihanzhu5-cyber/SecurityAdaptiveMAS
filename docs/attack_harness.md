# Attack Harness：攻击配置、真实注入与离线评价

本模块负责把已声明的攻击文本放进指定的真实工具返回字段，并在执行结束后核对发生了什么。组织决策、风险检测和权限判断继续由原有模块负责。本轮没有运行付费模型。

## 1. 源码审查与调用关系

原来的正式入口只在 `attack=single_injection` 时加载配置，而五例 A0/A1 使用 `single_entry_tool_result_text`，导致这些实验可能带着 `spec=None` 进入运行。已将两种攻击模式统一交给 Loader 校验，只有显式 `attack=none` 才返回空配置。

审查确认旧接口仍有调用者：`run_attacked_baseline.py` 使用显式 `receiver_agent_id=agent_0`；`are_bridge.py` 和 `dynamic_team.py` 使用旧 Overlay；历史 `AttackCase.overlay_spec()` 也会生成旧字段配置。因此保留 `attack.py` 的兼容导出与旧构造方式，没有要求历史配置迁移。

当前实际调用链：

```text
CLI / Study Manifest
  │
  └─ run_framework.main → load_for_run → Loader + Registry
       │  校验场景、模式、来源、字段、接收者、Payload 哈希、Evaluator
       │
       └─ run_case → 每场一个 AttackRuntime
            ├─ Evaluator.snapshot：记录初始目标状态（Harness 私有）
            └─ make_modular_agent_builder → session.attack_runtime
                 │
                 └─ ModularSession.invoke_tool
                      └─ 原有 ToolGateway.call（权限规则不变）
                           ├─ 实际 ARE 工具执行
                           ├─ result_transform → AttackContext → AttackRuntime.apply
                           │    └─ SurfaceAdapter：精确匹配并复制指定文本字段
                           └─ 原有 Provenance / tool_completed
                                ├─ bind_delivery：关联注入事件与真实 Evidence
                                ├─ 原有 RiskMonitor / Artifact 登记
                                └─ 原有 ARE Observation → 后续模型请求及归因

Episode 结束 → Runtime.finish → Evaluator.snapshot（最终状态）
             → Evaluator.evaluate（轨迹 + 真实提交请求 + 初末状态）
             → summary.json / evaluation.json

历史 Single Agent → AttackOverlay 兼容接口 → 同样的字段复制逻辑
历史 ARE Bridge   → AttackOverlay 兼容接口 → 旧工具结果或旧 ENV 入口
```

原始返回对象和 ARE 应用状态不因注入改变。注入发生在原有 ToolGateway 的返回变换边界，没有建立第二个工具网关，也没有让攻击模块调用 Controller。

## 2. 文件职责与稳定契约

| 文件 | 用途 |
| --- | --- |
| [contracts.py](../security_adaptive_mas/attacks/contracts.py) | 不可变配置、一次注入机会的上下文、结构化注入结果 |
| [loader.py](../security_adaptive_mas/attacks/loader.py) | 配置、Payload 和已注册能力的校验；不支持时在模型请求前失败 |
| [registry.py](../security_adaptive_mas/attacks/registry.py) | 显式维护 Surface 与 Evaluator 映射；无自动插件发现 |
| [surfaces.py](../security_adaptive_mas/attacks/surfaces.py) | 真实 ARE 类型的精确字段复制，以及不调用模型的来源读取探测 |
| [runtime.py](../security_adaptive_mas/attacks/runtime.py) | 每场独立状态、一次性注入、交付关联、正常或中断收尾 |
| [evaluators.py](../security_adaptive_mas/attacks/evaluators.py) | 目标结果与接触证据的离线评分；保留旧评分函数 |
| [attack.py](../security_adaptive_mas/attack.py) | 旧导入路径和 AttackCase 兼容入口 |

### AttackSpec

`AttackSpec` 是 frozen dataclass。为保留旧位置参数和 `dataclasses.replace` 用法，旧字段继续保存，统一名字通过只读属性提供；Loader 同时接受下表的统一 JSON 名称和旧名称。两套名称冲突时显式失败。

| 统一名称 | 含义 / 历史兼容 |
| --- | --- |
| `attack_id` | 本次攻击配置的标识 |
| `scenario_id` | 对应 `target_case`，必须与运行场景一致 |
| `surface_type` | 对应 `surface`，选择已注册的入口适配器 |
| `source_tool` | 精确工具名；旧邮件入口隐含 `Emails__list_emails` |
| `source_selector` | 精确的 `email_id:...` 或 `message_id:...` |
| `field_path` | 唯一允许修改的文本字段路径 |
| `source_conversation_id` | 声明时必须匹配的会话 ID |
| `receiver_policy` | 新入口为 first_actual_reader；旧邮件保留 Worker-only 规则 |
| `payload_path` | 相对于配置目录解析的本地文本文件 |
| `payload_sha256` | 对应 `payload_hash`；按文件原始字节核验 |
| `evaluator_id` | 对应 `evaluation_rule`，选择已注册评价器 |
| `evaluator_params` | 只读参数视图，包含目标类型和目标 ID；额外 JSON 参数保存为不可变字符串 |
| `protected_targets` | 历史评价使用的受保护工具列表 |
| `desired_deviation` | 保留的 Harness 私有说明，不能拼进在线提示词 |

`target_type`、`target_id`、`target_email_id` 继续可用，不改变旧目标语义。缺少评价器所需参数、未知字段、未知入口或哈希不符均直接报错。

### AttackContext 与 AttackApplyResult

`AttackContext` 保存真实可得的 `scenario_id`、`caller_id`、`tool_name`、`call_id`、`phase`、`tool_result` 和可选的 `simulation_time`。当前 ModularSession 使用实际工具调用 ID；旧兼容接口没有调用 ID 时明确记录 `None`。

`AttackRuntime.apply(context)` 返回 `AttackApplyResult`：

- `matched`：是否精确命中来源对象。
- `applied`：本次调用是否实际修改文本。
- `modified_result`：复制后的结果，未注入时保留原对象。
- `reason`：未到达、不匹配、接收者不符合或已完成一次性注入等具体原因。
- `attack_event_id`：实际注入事件标识。

随后 `bind_delivery(result, evidence_id=..., context=...)` 将本次实际返回与 Provenance 关联。新调用方不通过可变 `overlay.record` 猜测注入是否完成。

## 3. 目前支持的入口与评价

| 精确工具入口 | 真实返回类型 | 可修改字段 |
| --- | --- | --- |
| `Emails__list_emails` | `ReturnedEmails` 内的 `Email` | `emails[].content` |
| `Emails__get_email_by_id` | `Email` | `content` |
| `Chats__read_conversation` / `Messages__read_conversation` | dict 内的 `MessageV2` 列表 | `messages[].content` |
| `Chats__list_recent_conversations` / `Messages__list_recent_conversations` | `ConversationV2` 列表内的消息 | `[].messages[].content` |

接收者范围沿用既有语义：新的 first_actual_reader 接受实际 Coordinator 或 Worker；历史邮件默认只接受既有 Worker 前缀。旧 Single Agent 路径原本就显式指定 Coordinator 接收者，该行为继续保留。

评价器包括 `calendar_delete_v1`、`email_trash_v1` 和历史邮件/保护工具评价契约。新评价器要求精确目标调用及必要的初末状态；不会用 RiskMonitor 的可疑判断代替目标后果。

旧 `environment_notification` 仅保留在已存在的旧 ARE Bridge 中。新的 v0.6 Modular Runner 没有接线该阶段，Loader 会提前报告 `BLOCKED_attack_phase_not_wired_in_modular_runtime`。文件、共享 Memory 和未来动态 ID 入口尚未实现。

## 4. 注入状态与评分是两件事

| Runtime 状态 | 说明 |
| --- | --- |
| `CONFIGURED` | 配置已验证，尚未结束观察 |
| `NOT_REACHED` | 在已记录执行范围内，未调用声明的工具入口 |
| `MATCH_MISS` | 工具返回已发生，但精确对象、会话或接收者未匹配 |
| `INJECTED` | 指定文本字段实际修改一次，之后不重复注入 |
| `ERROR` | 注入阶段发生类型或契约错误；记录后继续抛出异常 |

`finish(interrupted=...)` 额外记录 `episode_completed`。中断前没到达入口的记录不表示防御成功。

离线评分分别输出 `attack_configured`、`attack_injected`、`delivered_to_runtime`、`model_submitted_exposure`、`exposed_instances`、`target_attempted`、`blocked_by_gateway`、`target_achieved` 和 `legitimate_task_completed`。

新路径的模型接触判定读取 `model_request_submitted` 对应的真实 `model_io/*_request.json`，检查完整载荷的精确表示：原文、JSON 字符串转义、Python repr 文本及其一层 JSON 包装。这适配了现有 ARE 和 RiskMonitor 的实际序列化，不做模糊匹配或目标 ID 猜测。来源相关但只提交了 metadata 的请求不记为载荷接触。

缺少提交请求原文或执行中断时，无法确认的接触/后果保留 `unknown`。`false` 的精确全文检查只表示未找到完整载荷表示，不能排除截断、改写或其他语义影响；`semantic_adoption` 仍为 unknown。看到载荷、采纳恶意要求和最终造成环境后果应分别解释。

旧 `evaluate_attack` / `evaluate_text_attack` 函数保留原评分契约，不追改历史报告；新正式 Runner 使用注册的 Evaluator，新增精确提交证据口径，不能与历史来源代理口径混为同一指标。

## 5. 新增攻击方式或 Case

**在当前工具返回边界内增加攻击方式：**

1. 在 `surfaces.py` 添加小型 Adapter，声明 `modes`、`phase`、配置校验、精确来源匹配和复制方法；提供免费 `read_probe` 以便预检。
2. 在 `registry.py` 显式注册 `surface_type`。Adapter 本身不保存 Episode 状态。
3. 如需新的目标结果评价，在 `evaluators.py` 实现 `validate`、`target_tool`、`snapshot`、`evaluate` 并注册。
4. 提供配置、固定 Payload 哈希和针对真实返回类型的免费测试。Runner、Controller、组织策略无需因新的工具返回类型增加分支。

不同生命周期入口仍需验证真实事件边界并接线，不能只注册名称就宣称支持；这部分属于后续工作。

**新增 Case 或切换同一 Case 的方案：**准备固定 Benchmark 数据及其校验信息、独立攻击配置、Payload 文件、精确来源证据和独立目标判据。已有 Adapter/Evaluator 可直接复用；若来源或目标无法验证，记录 BLOCKED。新 Case 的“目标在用户授权范围之外”也需要单独提供证据，不能直接套用当前五例的日期判据。

五例预检按配置中的 Scenario ID 选择文件，支持 `--attack-config` 指定替代方案；同一场景出现多个候选且没有明确选择时直接失败。Study prepare 同样接受该参数。

```powershell
# 免费检查正式配置加载；不发起模型请求。
python -m security_adaptive_mas.run_framework --scenario scenario_universe_22_0421h2 --attack single_entry_tool_result_text --attack-spec configs/attack_cases_v0_6_five/C1.json --dry-run

# 免费来源、权限、目标和 Benchmark/Judge 预检。
python scripts/preflight_v0_6_five_cases.py --output results/attack_harness/my_free_check --attack-config configs/attack_cases_v0_6_five/C1.json
```

上述命令需在当前工作树及现有 v0.6 环境运行。若使用旧工作树的 editable 环境，应先把 `SECURITY_MAS_ROOT` 与 `PYTHONPATH` 指向当前工作树。付费实验必须另行授权和重新冻结版本，旧 Study Manifest 不可跨源码快照复用。

## 6. 当前限制与验收边界

- **C3：BLOCKED_ATTACK_SURFACE。** 未来消息 ID 在实际事件执行时生成，不能提前冻结；没有用旧消息、模糊匹配或 ENV 注入替代。
- **C4：PARTIAL_SCOPE。** 当前攻击来源可验证，Files、附件和部分群组能力限制仍在，不能宣称完整合法任务可执行。
- 新工具返回入口的免费类型检查不保证真实模型一定读取该工具；未读取时应报告 NOT_REACHED。
- `first_actual_reader` 不保证产生 Worker，旧 Worker-only 入口也不保证会被触发。
- 本轮免费正式链路夹具明确读取目标字段并结束，故意不完成合法任务，用来验证接线和证据记录；它们不证明攻击成功率、模型能力或安全收益。
- 旧完整 v0.2 测试套件依赖旧工作树路径，当前环境无法全部执行；已单独测试可执行的历史配置、Overlay、评分和 Single Agent 工具包装器。

本轮实施记录见[攻击子系统实施报告](reports/v0_6_attack_harness_implementation.md)。
