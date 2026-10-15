# v0.6 实际架构

日期：2026-10-15。本文描述已写入代码并做非付费接线验收的路径；真实 API 状态见 [pilot 报告](reports/2026-10-15_v0_6_B1_json_mode_http_400.md)。

## 一条执行链

`security-mas run` → `api.run` → `run_framework.main/run_case` → 官方 `ScenarioRunner.run`。每场创建一个 ModularSession、DeepSeekClient/RunBudget 和应用环境，不创建旧 Controller。

```text
ARE 到达队列 ──→ OnlineObservationRecorder ──→ USER 目标/授权
      ↓
官方 Coordinator ──→ 应用工具 ──→ 同一 ToolGateway
      │
      ├─ delegate_task → WorkItem → 图预览/ATM/策略 → SPAWN/ASSIGN
      │                                      ↓
      │                    AOrchestra reset → step → Memory
      │                                      ↓
      │                           真实工具 → Artifact/来源
      │                                      ↓
      └─ 无正文回执 ←─────────────────────────┘
      │
      └─ get_artifact（Coordinator 或 Worker）
               ↓
          局部候选 / 风险 / ATM / 图预览
               ↓
          一次提交 → MANTA visible_packets → 路由 + ACL
               ↓
          正文 / 受限视图 / held / repairing
               ↓
          官方 ObservationLog UUID → 实际历史渲染 → submitted
```

## 模块有什么用

| 模块 | 实际职责 | 代码 |
|---|---|---|
| 每场会话 | 连接工具、工作、结果、授权、风险和日志 | `orchestration/session.py` |
| 数据契约 | 保存具体委托、不可变对象引用、消费请求和候选 | `orchestration/contracts.py` |
| 委托与消费工具 | 绑定调用者，严格检查列表，避免回执泄漏正文 | `orchestration/delegation.py` |
| Worker 执行 | 串行调用派生 ReActAgent 的 reset/step/Memory，通过共享工具网关执行 | `orchestration/worker_runner.py`、`adapters/aorchestra.py` |
| 交付 | 生成候选、选中后交付或执行一次补做 | `orchestration/delivery.py`、`orchestration/policy.py` |
| 生效图 | 有向路由、对象 ACL、承担者、实例上限、版本和 object_epoch | `graph_runtime.py` |
| MANTA 适配 | 将生效图投影为只读 spec，真实筛选待交付 packet | `adapters/manta_context.py` |
| ATM 适配 | 调用契约与 distill，核对空权限、deny、对象身份和准确去向 | `adapters/atm_mutation.py` |
| 风险观察 | 复用既有语义 prompt；只读实际证据与已到达 USER 目标 | `risk_monitor.py` |
| ARE 适配 | 官方生命周期、队列、工具映射、ObservationLog 和历史渲染钩子 | `adapters/are_modular.py` |

## 真实输入归因

Worker 使用上游 `_build_prompt`，在该次组装时记录指令、当前 USER 目标、观察和实际 Memory 的来源。句柄目录不自动展开正文。模型输出形成的记忆仍保留输入父链。

Coordinator 的执行器回调绑定实际 ObservationLog.id 与本次工具返回的引用；原 `build_history_from_logs` 真正读取这些日志时收集引用，并尊重排除参数与 role_dict。只有 on_submitted 才记录接触。其他无法逐项归因的官方文字标为 `official_rendering_unknown`，不通过全文搜索猜测。

委托回执的引用为空；get_artifact 的引用为实际放行的 full/view。官方自动超长截断关闭。无法绑定项目拥有的正文时真实终止，不能把正文归因缺口统统写 unknown 后宣称通过。

## 权限与生命周期

Coordinator 保留已选沙箱写工具，Worker 只持具体只读工具。`tools=[]` 不会变成全工具。用户通知更新目标和授权；ENV 不更新授权。

结果产生只授予生产者本地读取。交付需要实际句柄、精确对象/视图、有效版本、路由和 ACL。退休作者走显式 persistent_store，不冒充活跃 Agent；MANTA 使用本场已注册的历史作者占位视图，不能授予执行能力。

Worker 完成后 idle。资源压力下，只退休已有实际交付、没有运行中工作的 idle Worker；旧结果按存储交付规则继续可请求。历史接触不会因删边或退休而消失。

## 当前边界

检测器本身的模型请求同样可能接触可疑内容，以 risk_monitor 实例记录，不混为 Coordinator 接触。Coordinator 直接读应用结果也是直接暴露，不能声称所有信息都经过 Worker 交付策略。

官方 TimeManager 保留；日志记录模拟起止和实际 wall time。Worker 调用没有单独实现官方模拟生成时间扣计，报告明确标为未计入，不能据此声称模拟任务加速。
