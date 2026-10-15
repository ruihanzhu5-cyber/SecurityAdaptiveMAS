# v0.6 实施报告与问题台账

日期：2026-10-15。开发分支 `research/v0.6-modular-runtime`，基础 HEAD `56a30e320eb1017e6fa5f2e66320977c3e6e2a97`。实验运行时，v0.6 代码尚未提交；真实使用版本以当时的运行快照和未提交 diff 为准。本次整理将实现与失败报告一并提交到该分支，不将后来的 Git 提交冒充当时的执行版本。

## 1. 工作区与范围

新 worktree：`D:/SecurityAdaptiveMAS-v0.6`。旧目录、取消超时的原改动、四份 v0.5 实验报告和失败轨迹均留在原处，没有 reset/stash/clean/搬迁。

按第二份代码指令实施，第一份用于理解。新主链不创建旧 Controller/TaskProposer/JointRewriter，不枚举 13 类全图模板。没有增加新攻击载荷、RL、并发或完整状态恢复。

保留了用户取消自定义 45 秒超时的要求：本分支客户端不传 timeout，max_attempts=1。底层 LiteLLM 默认行为仍存在，不称为无限等待。

## 2. 逐文件改动

| 文件/目录 | 实际改动 |
|---|---|
| `orchestration/contracts.py` | WorkItem、ArtifactRef、请求、候选、stamp 与严格列表校验 |
| `orchestration/configuration.py`、`configs/research_v0_6.yaml` | v0.6 专用 schema，拒绝旧设计；显式本地路径和统一资源上限 |
| `orchestration/session.py` | 单场服务连接、真实 USER/ENV、具体委托、结果登记、能力和输入校验 |
| `orchestration/delegation.py` | 真实 ARE delegate/get 工具；any 参数后严格 list[str] 校验；调用者由运行时绑定 |
| `orchestration/worker_runner.py` | 真正调用派生 reset/step/Memory，精确只读工具、get、finish 分发和局部状态 |
| `orchestration/delivery.py` | 具体候选、ATM/图预览、一次选择、MANTA 与最终 ACL、真实重查/改派 |
| `orchestration/policy.py` | 同候选集合上的 task-only/risk-aware 确定性规则 |
| `adapters/aorchestra*.py` | 同客户端异步 callable、实际 prompt 组件和一次严格 JSON 解析 |
| `adapters/manta_context.py` | 只读投影、实际 visible_packets、历史作者存储读取 |
| `adapters/atm_mutation.py` | 固定源码 import/哈希、真实契约和 distill、集合与身份检查 |
| `adapters/are_modular.py` | 官方队列/工具映射/失败 stop、真实 ObservationLog UUID 与历史读取钩子 |
| `graph_runtime.py` | 切断 Controller，保留有向 preview/apply；加 object_epoch、总实例/补丁约束和精确存储读取 |
| `risk_monitor.py` | 保留语义 prompt，改显式依赖与场内缓存；不回写旧 Controller |
| `deepseek.py` | 默认一次尝试、取消自定义 timeout；本地记录不含认证信息的实际请求/响应 |
| `are_adapter.py` | 数据文件系统和新沙箱位置分开配置 |
| `run_framework.py` | 现有 run_case 换为 ModularSession/builder，保留一次官方运行、错误、导出和同一账本 |
| `api.py`、`cli/doctor.py`、`cli/report.py` | 同入口、新配置、真实源文件/缺项检查、新事件汇总 |
| `_vendor/`、`upstream_v0_6.lock.json` | 最小私有子集、原始字节、派生 diff、固定 hash 和许可材料 |
| `scripts/fetch_v0_6_sources.py`、`vendor_v0_6.py` | 固定源码获取与可复核派生过程 |
| `scripts/run_v0_6_pilot.py` | 四个 dry-run、冻结、B1→B0→A0→A1，同一账本且不自动重复 |
| `pyproject.toml`、README、v0.6 文档 | 版本、实际依赖、私有许可包数据和真实入口/边界 |

`security.py` 的 ProvenanceStore/MessageGateway/ToolGateway 沿用现有实现；新入口传入同一生效图，没有放宽其权限。原 `online_observation.py`、攻击载荷/selector/Oracle、数据与历史日志未改。

## 3. 原模块是否真的调用

- **AOrchestra：**新 Worker 调用固定派生 ReActAgent.reset、step、_build_prompt 和 Memory.add_memory/as_text。测试捕获了真实方法调用；不使用其空 run 或原 DelegateTaskTool。
- **MANTA：**每次授权正文交付调用 visible_packets，使用实际返回包；同 sender 的不同对象都保留。删有向边或 ACL 后不能继续交付，未知作者不能借 MANTA 的默认放行分支进入。
- **ATM：**每次候选结构验证真实调用 verify_invariants 和 distill；提交消费准确 destinations。数量相同但对象身份重复也会失败。
- **ARE：**非付费完整生命周期测试使用官方 Runner、真实场景、真实 ReturnedEmails、官方日志 UUID 和原历史渲染器。模型回复是明确 fixture，不是研究模型成绩。

## 4. 测试与诊断证据

独立环境最终检查为 **15 passed / 3 warnings / 29.48 秒**：其中 13 项 v0.6 聚焦行为测试，另有 2 项仍适用的 DeepSeek 路由/预算/解析测试。3 个警告来自 Pydantic 上游弃用项。本地输出为 `work/v0_6_tests_final.txt`，临时沙箱显式位于新 D 盘工作区，不是付费实验结果。

覆盖：真实源码方法、同一费用客户端、空工具、精确状态路由、有向边/ACL、版本与 epoch、无正文回执/HOLD、官方邮件投影、Worker A→B、实际改派读取、prepared/submitted 区分、官方日志归因及终止错误。

初次接线诊断使用旧解释器依赖，结果为 9 passed / 1 failed；失败是 repairing 状态被 work 回执覆盖。修正后在独立新环境验收，不把旧解释器诊断和最终结果相加。

完整官方生命周期 fixture 曾误用该 Coordinator 不提供的 final_answer，真实报 UnavailableToolAgentError。已修正 fixture 为官方 AUI 回复结束回合；没有给生产代码添加工具名替代或降级。

原 v0.5 测试文件保留，内部算法/旧工作路径测试属于 v0.5 基线，未声称新分支应原样通过 109 项。上述 2 项适用测试已单独计入最终 15 项，不能再相加；没有删除出错旧用例凑全仓通过。

## 5. 实际策略差异的证据口径

离线受控输入中：字段足够时两个方法都能选邮件元数据视图；请求完整可疑结果时 task-only 原样交付，risk-aware 选择同来源重查；没有可重放调用时，risk-aware 能创建新实例接手原工作并实际读工具。修复次数达到上限时返回无正文 held。

这些说明实现路径可被执行，不说明真实 DeepSeek 会产生同样提案或攻击影响。真实方法差异与付费状态见 [pilot 报告](2026-10-15_v0_6_B1_json_mode_http_400.md)。

## 6. OPEN/BLOCKED 台账

| ID | 位置 | 已核实事实 / 原问题 | 影响与最小后续 |
|---|---|---|---|
| OPEN-LICENSE | ATM 固定源码根目录 | 根 LICENSE 缺失，README/pyproject 声明 MIT | 本地依赖；再分发前补齐上游材料，不能编造版权文本 |
| OPEN-TASK | summary/evaluation | 无通用独立任务完成 checker | 合法任务完成 unknown，不能用官方 success 替代 |
| OPEN-SOURCE | REQUERY/REASSIGN | 重查同源；现有 overlay 只注入一次 | 新结果再审核，不能称为独立可信来源 |
| OPEN-ATTR | ARE composer | 项目自有正文精确绑定，其他官方文本部分未知 | 单列 official_rendering_unknown，不能宣称全系统无旁路 |
| OPEN-TIME | WorkerRunner | 未单独接官方模拟生成时间扣计 | 保留模拟起止/真实 wall time，不宣称模拟加速 |
| OPEN-COST | 候选成本 | 步骤/字节/新实例是代理，费率为历史估算 | 用真实 usage 报成本，不声称校准未来 Token |
| OPEN-SEMANTICS | 风险/视图 | 投影不含正文，父链仍外部；摘要不等于去污 | 分开报告来源相关接触、实际载荷和语义采纳 |
| OPEN-EXPOSURE | Coordinator 应用工具 | 仍可直接读外部内容 | 直接暴露保留，不强制 Worker-first 或抹除历史 |
| BLOCKED-JSON-MODE | `risk_monitor.py::assess` / `DeepSeekClient.complete` | 首次真实风险请求启用 json_object，但沿用 prompt 没有 json 一词；API 明确返回 HTTP 400 | 首个 Coordinator 请求已成功；这是项目请求契约问题。按指令停止批次，下一轮可审查增加明确 JSON 输出措辞并更新 prompt 版本，本轮不修改源码/重跑 |

## 7. 当前交付状态

新主链代码、非付费真实接口测试和文档已形成可审查修改。用户确认费用后已执行 B1：提交 2 个真实请求，Coordinator 成功响应 1 个，风险请求 HTTP 400。未产生真实委托或图补丁，B0/A0/A1 停止；不能把离线完整链当成这次真实闭环已跑通。源码冻结后未修改代码，没有提前写安全/性能提高结论。实验结束时尚未 commit、push；本次按用户要求将代码与报告提交至独立 v0.6 分支，不合并 main。已确认问题见 [GitHub Issue #3](https://github.com/ruihanzhu5-cyber/SecurityAdaptiveMAS/issues/3)，尚未修复。

## 8. 发布整理检查

将两份报告统一命名为日期、版本和主题，补充当日日志与 GitHub Issue #3 链接；本轮没有重新运行测试或付费实验，15 passed / 3 warnings 来自上文已有验收记录。检查了本轮文档的 24 个相对链接，均指向现有文件（未验证页内锚点）。

暂存内容检查未发现凭据模式、超过 1 MB 的文件或 work/results/.venv 目录内容；MANTA/AOrchestra 的派生文件和原始副本哈希全部与锁文件一致。补充 `.gitattributes` 保持私有上游子集的原始字节，避免 Windows Git 换行转换破坏哈希；保留上游空白，不为通过空白检查改写原件。该调整属于发布打包设置，实验运行快照保持原样。
