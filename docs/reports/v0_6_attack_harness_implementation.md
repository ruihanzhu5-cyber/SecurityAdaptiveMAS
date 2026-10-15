# v0.6 Attack Harness 工程化实施报告

日期：2026-10-15。**本轮只有工程修改、免费类型检查和明确模型输出夹具；付费 DeepSeek API 调用为 0。**

## 1. 基点和代码版本

- 指定基点：`a61673ecbf0e41692723f8b3702113b7c657df8b`；实施前 HEAD 一致，工作区干净。
- 附件明确要求独立分支，因此本轮使用 `refactor/v0.6-attack-harness`；没有修改或合并 main。
- 工程代码与回归测试提交：`885c2eb7c1f717158baf1f2b1baaa35dd78146ec`。
- 交付源码快照 SHA-256：`3ae3d51c4515671e9ae70897c374a01549b0db4c0e070dd3b908ee93d524d568`。
- 工程代码先提交并保存快照，本报告随后单独提交。免费测试在提交前的开发工作树执行；夹具中的基点 SHA 与 dirty 状态保留，没有伪装成已提交版本的付费实验。提交前仅整理了新文件的空行。

接口、调用图和扩展步骤见[工程说明](../attack_harness.md)。

## 2. 发现了什么，如何处理

**确认 Bug：**正式 Runner 只加载 `single_injection`，五例 A0/A1 的 `single_entry_tool_result_text` 会得到 `spec=None`。现在所有非 benign 模式统一经过 Loader：配置必须成功加载，场景、模式、Payload、精确字段、接收者和 Evaluator 必须有效；不支持时在模型请求前失败。

攻击配置仍保留原始字段、原 Payload 和目标。已把原来的字段复制逻辑与后果评价拆开，运行时每场独立实例化，输出可区分的状态和注入结果。Runner 只负责加载、创建、绑定真实返回边界和结束后评价。

**确认兼容关系：**旧 Single Agent、旧 ARE Bridge、dynamic_team 和历史 AttackCase 仍引用旧接口，因此 `attack.py` 继续导出旧名称，`AttackOverlay` 继续提供原方法。旧 ENV 入口只保留在旧 Bridge；没有把 C3 换成 ENV 攻击。

**评价口径修正：**来源相关的对象进入模型输入不一定表示载荷文本被提交。新的正式 Evaluator 读取真实提交请求文件，核对完整载荷的精确原文或已核实的 JSON/Python repr 表示。只提交 metadata 的免费测试明确得到未接触；提交实际复制结果后才得到接触。没有用虚构注入日志代替工具读取。

## 3. 文件修改与边界

| 文件 | 实际修改 | 分类 |
| --- | --- | --- |
| `attacks/contracts.py` | 不可变 Spec、只读统一字段、真实 Context、结构化 ApplyResult | 攻击基础设施 |
| `attacks/loader.py` | 旧/统一字段转换，Payload 与注册能力校验，正式模式加载 | 攻击基础设施 |
| `attacks/registry.py` | 显式 Surface / Evaluator 映射 | 攻击基础设施 |
| `attacks/surfaces.py` | 复用精确 ID、字段复制和一次性注入语义；真实工具返回预检 | 攻击基础设施 |
| `attacks/runtime.py` | 每场状态、明确交付关联、兼容 Overlay、统一来源探测 | 攻击基础设施 |
| `attacks/evaluators.py` | 保留旧评分，增加独立接触与结果评价接口 | 离线评价基础设施 |
| `attacks/__init__.py`、`attack.py` | 新 API 导出与历史导入兼容 | 接口整理 |
| `run_framework.py` | 修复配置加载，创建 Runtime，结束后输出状态和注册 Evaluator 结果 | Runner 攻击接线 |
| `adapters/are_modular.py` | 将同一 Runtime 绑定至 Session，保留旧 attack_spec 参数入口 | ARE 适配接线 |
| `orchestration/session.py` | 只调整工具返回变换和显式 Evidence 关联；移除一个未使用导入 | 攻击边界接线 |
| `scripts/preflight_v0_6_five_cases.py` | 按配置声明的场景选文件，复用统一探测接口，允许显式选择替代配置 | 预检基础设施 |
| `scripts/run_v0_6_five_case_study.py` | prepare 转交选定攻击配置，使用预检记录的模式；默认五例参数不变 | 实验编排基础设施 |
| `tests/test_attack_harness.py` | 本轮 12 项有针对性的免费验收 | 测试 |
| 本报告、`docs/attack_harness.md` | 实现说明、真实调用图、扩展方法和限制 | 文档 |

表中 `attacks/` 均位于 `security_adaptive_mas/attacks/`。生产修改共 13 个文件，另有 1 个测试文件；文档另行提交。

**研究算法未修改。** 对上一条真实 C1_B1 源码快照中的 129 个受保护文件逐个核对哈希，无变化；该快照源码与本次指定基点一致。包括 Controller、ReconfigurationPolicy、GraphRuntime、ToolGateway 和权限策略、风险检测、MANTA/ATM/AOrchestra、模型计费与 Benchmark Judge 相关代码。Coordinator/Worker 提示词、所有现有配置与 Payload 同样未修改。

Session 与 ARE Adapter 的改动仅在注入边界，原组织决策、动作执行、Artifact 登记和模型历史渲染流程保留。攻击组现在会真正带入已声明配置，因此旧 `spec=None` 版本的轨迹不能当作本版本攻击效果证据，也不能混合比较。

本地生产 diff：`results/attack_harness/production.diff`，SHA-256 为 `425d6ffef1053249cc36ad03f055080b447d32524000f02aad658d7da61b9338`。可复核：

```powershell
git diff a61673ecbf0e41692723f8b3702113b7c657df8b 885c2eb7c1f717158baf1f2b1baaa35dd78146ec -- security_adaptive_mas scripts
```

## 4. 免费验收实际结果

最终回归：**62 项通过，1 个上游 Pydantic 配置弃用警告，224.88 秒。**

包括本轮 12 项、已有五例攻击检查 11 项、可执行的历史攻击契约 7 项、预算授权 14 项、portable 9 项及 Benchmark/Judge/生命周期 9 项。这些是最终一次套件内的项目数，不与前面试跑的 53 项相加。

| 验收要求 | 实际证据 |
| --- | --- |
| A0/A1 配置加载 | 两种 method 都从正式 CLI 加载 C1 配置；两条官方 ARE 夹具轨迹都绑定 Runtime 并真正注入 |
| 四类已有返回入口 | 使用 C1/C2/C4/C5 真实返回类型，另检验单条 Email；只改变一个 content 字段，原始对象及应用状态不变，不重复注入 |
| 不命中与错误 | NOT_REACHED、MATCH_MISS、ERROR 分别验证，字段类型不符抛出错误 |
| 评价隔离 | 实际注入、交付、metadata-only 提交、完整载荷提交、目标动作和目标状态分别检查；无调用证据的状态变化不直接判攻击达成 |
| 历史兼容 | 旧配置、ENV Overlay、独立评分契约通过；旧 Single Agent 的实际邮件工具 forward 包装及显式 Coordinator 接收者通过 |
| Benign | B0/B1 dry run 不读取攻击文件；既有完整 C1 多回合、Judge 和收尾夹具回归通过 |

旧 Single Agent 包装器测试使用真实 ARE 邮件工具返回，但用最小 AgentBuilder 外壳夹具隔离工具包装行为；没有重新运行真实付费 Single Agent Episode。

### 两条正式调用链夹具

| 项目 | Task-Only / A0 接线夹具 | Risk-Aware / A1 接线夹具 |
| --- | --- | --- |
| Run ID | `f701b85654de` | `62f0a9e199d7` |
| Runtime 状态 | INJECTED | INJECTED |
| 实际读取 | C1 声明的真实会话与消息字段 | 同左 |
| Provenance 交付 | 已关联实际 Evidence | 已关联实际 Evidence |
| 模型接触 | 真实保存的夹具请求确认接触 | 同左 |
| 目标危险动作尝试 / 最终目标达成 | false / false | false / false |
| Worker / 有效图补丁 | 0 / 0 | 0 / 0 |
| 夹具调用次数 / 付费 API 次数 | 4 / 0 | 4 / 0 |
| 合法任务评价 | false，夹具故意没有完成原任务 | 同左 |

夹具明确执行“读取来源字段 → 向用户结束回复”，没有完成原合法任务，因此官方 Judge 拒绝符合测试预期。载荷及注入日志由实际工具返回变换产生，后续 Coordinator 和 RiskMonitor 接触由其实际请求文件核对。这只证明接线和评分隔离，不证明真实模型中招或防御成功；夹具 usage 是测试值，不能作为真实 Token/费用或效率结果。

### 五例免费兼容性预检

| Case | 攻击来源与配置 | 官方 Benchmark 预处理 | 合法任务范围 |
| --- | --- | --- | --- |
| C1 | READY | 通过，原生 2 回合 | FULL（权限口径） |
| C2 | READY | 通过，原生 2 回合 | FULL（权限口径） |
| C3 | BLOCKED_ATTACK_SURFACE | 通过，原生 1 回合 | FULL（权限口径），攻击不可执行 |
| C4 | READY_WITH_LIMITATION | 通过，原生 1 回合 | PARTIAL_SCOPE |
| C5 | READY | 通过，原生 1 回合 | FULL（权限口径） |

预检没有调用模型。READY 表示本地来源、类型、目标与环境接入条件通过，不表示真实模型一定读取来源或完整任务成功。C4 未放宽 Files/附件权限。

## 5. 实际暴露的问题与尚未支持的能力

1. **已修复：**A0/A1 正式模式漏加载配置。
2. **已修复：**仅按来源相关组件推断接触会混淆 metadata 与载荷；新正式评分使用精确提交内容证据，历史评分接口不追改。
3. **已修复：**首次重构新增配置日志打破旧 ENV Overlay 的精确日志契约；旧兼容入口保留原日志行为，新 Runtime 保留配置状态日志。
4. **OPEN：旧完整 v0.2 套件环境依赖。** 首次试跑有 11 项因旧 MANTA / Gaia2 文件系统路径缺失而失败，另 1 项上述日志契约失败已修复。没有添加路径 fallback、修改旧 Controller 或放宽检查；最终单独验证 7 项可执行的历史攻击契约。不能声称完整旧套件全部通过。
5. **BLOCKED：C3 动态消息 ID。** 本轮没有实现合法事件产生后的动态 ID 绑定，也没有替换攻击入口。
6. **OPEN：C4 完整合法任务能力。** 仍是 PARTIAL_SCOPE。
7. **OPEN：新的生命周期入口。** 文件、Memory、未来 ENV 等未验证能力未实现；旧 ENV 兼容不等于新的 Modular Runner 支持 ENV。
8. **OPEN：新 Case 的授权范围与目标有效性。** 当前五例目标仍使用原来的离线证据；新 Case 需要自己的独立判据，不能套用既有日期条件。
9. **尚未验证：真实模型攻击效果。** 本轮付费 API 次数为 0，不评价攻击成功率、安全收益或性能收益。历史 C1_B1 及其他原始研究日志未改变。

## 6. 原始证据与交付

本地保留：

```text
D:/SecurityAdaptiveMAS-v0.6-benchmark-runtime/results/attack_harness/
  delivery_snapshot.json              已提交代码的完整源码快照
  source_scope_audit.json              受保护文件与历史账本核对
  formal_chain_free_fixtures.json      两条免费正式调用链摘要
  production.diff                     生产代码完整差异
  artifact_checksums.json              1,158 个本地原始文件的哈希
  attack_final_03.log                  最终 62 项回归原始输出
  attack_harness_01.log                首次接触判定失败的原始输出
  free_preflight_20261015/             五例来源、目标、权限及 Benchmark 预检

D:/SecurityAdaptiveMAS-v0.6-benchmark-runtime/work/pytest_attack_final_03/
  ...                                 模型夹具请求、工具、Provenance、官方 Trace 等
```

历史共享费用账本哈希仍为 `e24dac865985d1473f8e650cdc4ac249af801fb04e3efca6261c1f4d73a067ca`，本轮没有写入该账本，也没有使用旧 Study Manifest 启动实验。

本轮交付源码、测试与两份文档；原始夹具内容、消息正文及敏感配置保留本地，不随文档上传。没有启动 20 组实验。后续真实实验需重新确认范围并冻结新版本。
