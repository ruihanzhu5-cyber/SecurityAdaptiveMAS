# Security-Aware Adaptive Multi-Agent Organization v1

这是固定五个 Gaia2 场景的离线可验证系统骨架。现有演示使用 mock 工具；不会运行付费模型，也不会把 mock 结果当作 Gaia2 任务成功。

## 最小集成方案

1. `configs/gaia2_v1_five.yaml` 固定五个 ID、官方数据修订版与本地路径。`config.py` 从已有 manifest 加载原始 JSON，并核对 SHA-256。拓扑控制器只接收当前任务与已观察到的执行状态，不接触未来事件或官方验证器。
2. `manta_adapter.py` 直接加载官方 MANTA 的 `MAS.self_evolved.spec`，使用其 `TopologySpec`、`ContextPolicy` 和 `TopologyMutation`。Adapter 避开 `MAS.__init__` 对 LangGraph 的 eager import；发布前显式收紧所有 Agent 权限。
3. `Controller` 根据当前阶段和未完成子任务比较 Singleton、Star、Chain；组织版本、候选分数、变更原因写入 JSONL。消息与工具调用都经网关。
4. `are_adapter.py` 使用官方 `JsonScenarioImporter`、`ScenarioRunner`。`are_bridge.py` 通过官方 `AgentBuilder` 接口安装工具网关。当前真实 ARE 接入只支持单 Agent、明确允许的只读工具；有状态写入保持关闭，直到完成行动级来源追踪与真实多 Agent 调度。

## 离线运行

在本目录执行：

```powershell
python -m pip install -r requirements-v1.txt
python -m pytest tests -q -p no:cacheprovider
python -m security_adaptive_mas.demo --scenario scenario_universe_22_0421h2 --trace results/demo.jsonl
```

允许的五个 ID 在 `configs/gaia2_v1_five.yaml`。`demo` 会验证原始场景文件的哈希，但并不加载 ARE Universe 或使用原始场景任务目标作为 mock 任务。结果中的 `mode=offline_mock` 是明确标记。

## 可选 ARE 只读 smoke 入口

当前 D 盘环境 `D:\SecurityAdaptiveMAS\.venv` 已安装官方 ARE `requirements.txt`，并通过五场景官方 JSON 导入器与 `ScenarioRunner` 构造检查。真实模型执行仍需准备模型 API 配置，以及明确工具名与 App 允许列表。`are_smoke.py` 调用官方 `ScenarioRunner`，保留官方环境、Validator 和轨迹导出；其工具网关只放行 `write_operation=False` 的工具。未知操作类型按写入处理。

```powershell
python -m security_adaptive_mas.are_smoke --scenario scenario_universe_22_0421h2 --model MODEL --provider PROVIDER --allow-read-tool TOOL_NAME --allow-app APP_NAME --acknowledge-model-cost
```

此命令可能产生模型费用，当前未执行。这个入口只用于验证 ARE 接线与真实工具读取，不代表可完成需要写入的 Gaia2 任务。完整真实执行还需实现动态多 Agent 调度、行动与来源的因果绑定，并完成 ARE 依赖安装及端到端验证。

详细现状见 `implementation_report_v1.md`。
