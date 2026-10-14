# Security-Aware Adaptive Multi-Agent Organization v1 实现报告

日期：2026-10-11

## 结论与范围

已交付可运行、可测试的离线多 Agent 组织控制骨架，包含固定五场景配置、MANTA 拓扑适配、Singleton → Star → Chain 动态切换、消息与工具网关、来源记录、模拟风险触发和受控重组。**没有执行真实 Gaia2 任务，也没有使用付费 LLM API。** 当前真实 ARE 桥接仅有只读单 Agent smoke 入口，尚不能宣称 Milestone 1 的真实多 Agent 集成完成。

五个固定 ID：

| ID | 类别 | 原始 JSON SHA-256 前 12 位 |
|---|---|---|
| `scenario_universe_22_0421h2` | adaptability | `c0e744908ac7` |
| `scenario_universe_21_y6td7z` | adaptability | `6892dec9822f` |
| `scenario_universe_30_erta5d` | time | `00da7d515348` |
| `scenario_universe_25_27a695` | execution | `d60eda8bc87b` |
| `scenario_universe_22_kb5ah9` | execution | `40b69ce555fa` |

配置在 `configs/gaia2_v1_five.yaml`。场景数据仍为先前下载的原始 JSON，运行时逐文件校验完整 SHA-256。没有修改场景事件、原始合法任务目标或官方成功条件。模拟流程没有读取官方未来事件和 Oracle 最终答案，也不根据场景 ID 选择拓扑。

## 官方代码复用与许可

| 来源 | 本地位置与版本 | 实际使用或接入点 | 许可 |
|---|---|---|---|
| [ARE 官方仓库](https://github.com/facebookresearch/meta-agents-research-environments) | `work/facebookresearch-meta-agents-research-environments-a9fd2ed`，commit `a9fd2edccb4bc7e22fbf45c8482a5fef30a2a5a4` | `JsonScenarioImporter`、`ScenarioRunner(agent_builder=...)`、`AgentBuilder`、`AppToolAdapter` 的调用边界；官方 `scenario.validate(env)` 和轨迹导出由 `ScenarioRunner` 保留 | MIT |
| [Gaia2 官方数据](https://huggingface.co/datasets/meta-agents-research-environments/gaia2) | revision `78ea3bdbdeec2bdcd6afa5420915d8a22f23ed99` | 固定五个原始 JSON，manifest 哈希校验 | 按数据集原许可 |
| [MANTA 官方仓库](https://github.com/mao-code/MANTA/commit/3da4087) | `work/mao-code-MANTA-3da4087`，commit 短 SHA `3da4087` | 实际运行 `MAS.self_evolved.spec` 内的 `TopologySpec.validate`、`ContextPolicy`、`TopologyMutation.apply`（`add_agent`、`set_group_pattern`、`add_edge`） | MIT，Copyright 2026 The MANTA Authors |
| [G-Safeguard](https://github.com/wslong20/G-safeguard) | 无本地拷贝 | 仅参考拓扑干预思路；未复用 GNN 或训练代码 | 未引入代码 |

没有修改 ARE 或 MANTA 官方源码。MANTA 的包顶层会 eager import LangGraph；当前环境没有该依赖，因此 `manta_adapter.py` 把官方 `MAS`/`MAS.self_evolved` 目录作为 namespace package 加载，仍直接执行官方 `spec.py`。没有使用 MANTA 的 Engine、Planner、Trace Auditor 或 `transaction.py`：其模型栈与 Gaia2 有状态写操作的重试语义尚未完成适配。新增的选择分数、来源网关与写入指纹逻辑属于本项目代码。

## 新增项目结构

```text
configs/gaia2_v1_five.yaml
security_adaptive_mas/
  config.py              固定场景与哈希
  manta_adapter.py       官方拓扑模块最小加载适配
  organization.py        任务状态、规划、候选评分、通信边
  controller.py          组织版本与受控切换
  security.py            来源、消息网关、工具网关
  trace.py               JSONL 审计
  demo.py                离线 mock 多 Agent 流程
  are_adapter.py         官方 JSON 导入器和 ScenarioRunner 入口
  are_bridge.py          官方 AgentBuilder 工具接线
  are_smoke.py           可选真实 ARE 只读 smoke 命令
tests/test_system.py
tests/test_are_bridge.py
requirements-v1.txt
README.md
```

## 已实现能力与验证结果

- **固定开发集**：只接受配置中的五个 ID，并核对 manifest 及原始 JSON 的 SHA-256。
- **动态组织**：Controller 根据传入的当前阶段、未完成子任务数和风险状态，对三种候选做硬约束检查和可配置加权评分。MANTA mutation 生成节点与模式，发布前将 `allowed_tools=None` 改为明确白名单；Worker 无工具权限，不继承全局证据。
- **来源与网关**：工具输出按低可信记录；派生摘要继承其父证据风险。消息需满足当前组织版本、通信边、接收者可见范围、来源拥有者和文本一致性。写工具需当前身份、工具与资源授权、可信任务证据和未重复的参数指纹。
- **安全重组**：模拟低可信证据试图触发写入时，网关拒绝并产生风险事件。Controller 进入受限 Chain、隔离原 Worker、为 Verifier 添加直达协调者的边；旧版本在途消息丢弃、旧版本工具调用被拒绝，写入指纹跨重组保留。
- **离线演示**：一条 trace 实测 4 次组织选择/变更、2 条传递消息（协调者→Worker→协调者）、1 个风险事件、1 次拒绝写入和 1 次经任务证据授权的 mock 写入。演示顺序为 `singleton → star → chain → chain`；最终 `mode=offline_mock`。
- **测试**：`python -m pytest tests -q -p no:cacheprovider`，**9 passed**；`python -m compileall -q security_adaptive_mas` 成功。用 mock 验证了只读 ARE 工具代理的放行与写入/未知类型拒绝。未以 mock 测试结果推断真实 Gaia2 成功率。

## 依赖与运行命令

离线骨架需要 Python 3.11+、PyYAML、pytest 和 `typing-extensions`。2026-10-12 已把项目复制到 `D:\SecurityAdaptiveMAS`，其中 `python\` 为 Python 3.12.13 运行时、`.venv\` 为独立隔离环境。D 盘环境安装了 ARE 官方 `requirements.txt` 的固定版本；`uv pip check` 显示 98 个已安装包兼容。离线测试在 D 盘 **9 passed**；ARE 官方 `JsonScenarioImporter` 已成功逐一加载五个固定 JSON，`ScenarioRunner` 可构造。**未运行官方 Universe/Validator 的完整场景回合，也未调用模型。** 原 C 盘 `work/.venv` 的依赖不完整，但已不再用于 D 盘项目。

```powershell
python -m pip install -r requirements-v1.txt
python -m pytest tests -q -p no:cacheprovider
python -m security_adaptive_mas.demo --scenario scenario_universe_22_0421h2 --trace results/demo.jsonl
```

要试真实 ARE 的只读接线，可使用已就绪的 D 盘环境，准备模型名、provider、API 密钥或 endpoint，以及确切的 App 和只读工具白名单。然后运行 `README.md` 的 `are_smoke` 命令；必须显式传 `--acknowledge-model-cost`。该命令会调用真实模型，费用取决于所选服务和实际 token，当前未选择服务，不能可靠给出数值估算，也没有执行。真实运行会设置 `oracle=False`、`export=True`，官方 Validator 与轨迹导出保持在 `ScenarioRunner` 中。

## 尚未实现和下一阶段

1. **完整真实 ARE 多 Agent 调度**：当前 LLM 接口只接了官方单 Agent builder，离线多 Agent 组织由 mock 模拟。需要让真实 Worker/Verifier 在 ARE 通知与时间推进中协作，并持续向 Task Analyzer 提供已观察到的状态。
2. **行动级来源因果绑定**：目前离线模拟显式传证据 ID；真实模型行动尚无法可靠绑定其所依赖的工具输出。因此 `are_smoke` 坚持只读，不能完成含写操作的正式任务。需先实现模型上下文与行动证据追踪，再安全放行经任务授权的写入。
3. **跨进程写入保护**：当前写入指纹仅在进程内保存，调用异常时保留 pending 状态阻止模糊重试。正式实验需要持久化事务日志、远端状态对账及多进程并发语义。
4. **自动任务分析与指标**：目前阶段和子任务由调用方明确传入，尚无从真实 ARE 轨迹自动抽取依赖/并行机会的模块；Token、延迟、合法任务成功率与误拦截等真实指标尚未测量。
5. **攻击 Overlay 与四策略对比**：未修改官方场景；后续可独立实现低可信边界替换、攻击成功指标，以及同模型同预算的四策略对比。G-Safeguard 的 GNN 不在 v1 范围。

开始真实执行仍需要：可安装的 ARE 环境、选定的模型与 API 配置、对费用预算的确认，以及任务级 App/工具授权策略。无需重新筛选这五个场景。
