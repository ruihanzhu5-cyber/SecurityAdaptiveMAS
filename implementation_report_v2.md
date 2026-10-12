# Security-Aware Adaptive Multi-Agent Organization v2 实施报告

日期：2026-10-12。工作目录：`D:\SecurityAdaptiveMAS`。

## 结论

已在 D 盘完成固定五场景的可校验恢复路径、安全网关修复、DeepSeek V4.1 Flash 的固定路由适配、ARE 官方 Runner 的只读接线，以及嵌入 ARE 工具循环的动态团队实现。`bootstrap.ps1 -SkipDownloads` 在当前目录校验了五个 JSON，离线测试 **22 passed**。ARE 离线探测已初始化 Universe，进入官方事件循环并导出轨迹。**没有真实 DeepSeek API 调用，费用为 0；真实 Single Agent 与动态 Multi-Agent 尚未在线端到端验证，不能报告 Gaia2 成功率。**

## 实际修改的文件

| 文件 | 改动 |
|---|---|
| `configs/gaia2_v1_five.yaml`、`configs/five_manifest.json`、`configs/source_versions.json`、`security_adaptive_mas/config.py` | 固定五个 ID、相对路径、官方修订版及逐场景 SHA-256；去掉原开发机绝对路径 |
| `bootstrap.ps1`、`requirements-v2.txt`、`security_adaptive_mas/bootstrap_data.py` | 固定源码/数据获取、Parquet 与场景校验、D 盘环境安装、离线测试 |
| `security_adaptive_mas/security.py`、`controller.py`、`organization.py` | 行动级授权、消息双向隔离与版本/边检查、只读角色权限、Verifier 可达性、写入日志 |
| `security_adaptive_mas/are_adapter.py`、`are_bridge.py` | 官方 Scenario 初始化、ARE 工具代理与运行时可见输入/输出来源记录 |
| `security_adaptive_mas/deepseek.py`、`deepseek_probe.py` | 固定 DeepSeek 路由、预算/usage/超时、单请求连通性入口 |
| `security_adaptive_mas/are_smoke.py`、`are_multi.py`、`dynamic_team.py` | 官方 Runner 单 Agent 入口、动态团队入口及只读 Worker/Verifier |
| `security_adaptive_mas/trace.py`、`demo.py` | 日志模式、离线任务授权样例 |
| `tests/test_system.py`、`tests/test_deepseek.py`、`tests/test_dynamic_team.py`、`README.md` | 回归测试、运行说明 |

没有改动五个官方 Scenario 的原始内容、官方动态事件或 Validator，也没有改动 ARE/MANTA 源码。

## 固定数据与可复现性

五个 ID 保持：`scenario_universe_22_0421h2`、`scenario_universe_21_y6td7z`、`scenario_universe_30_erta5d`、`scenario_universe_25_27a695`、`scenario_universe_22_kb5ah9`。逐场景 SHA-256 存在 `configs/five_manifest.json`，启动和选择场景时验证。ARE commit `a9fd2edccb4bc7e22fbf45c8482a5fef30a2a5a4`；MANTA short commit `3da4087`；Gaia2 revision `78ea3bdbdeec2bdcd6afa5420915d8a22f23ed99`；文件系统 revision `132e26376f5e963bb59f64bcccdd02188cb08dee`。三个 Parquet 的字节数和 SHA-256 固定在 `configs/source_versions.json`。

当前 D 盘源码目录是从归档迁移的快照，**没有各自的 `.git` 元数据**。因此本轮可验证五个 JSON 和使用这些源码执行测试，但不能声称已用当前目录完成 Git commit 身份验证。完整 `bootstrap.ps1` 对新检出的项目执行 Git checkout 与 SHA-256 校验；遇到当前快照会明确报错。`-SkipDownloads` 是当前 D 盘路径，检查已有五个 JSON，不假装验证源码 Git 身份。完整联网 bootstrap 尚未从空目录实测。

## DeepSeek 最终配置

| 项目 | 生效值 |
|---|---|
| Provider / Model ID | `deepseek` / `deepseek-flash`（展示名称 DeepSeek-V4.1-Flash） |
| API Base / 密钥 | `https://api.deepseek.com` / 环境变量 `DEEPSEEK_API_KEY` |
| 路由 | ARE `LiteLLMEngine` 子类调用 LiteLLM，显式 `custom_llm_provider=deepseek`；错误 Provider/endpoint 直接报错 |
| 模式 / 参数 | `thinking=disabled`、`temperature=0`、`max_tokens=4096`、超时 45 秒 |
| 重试 | 仅限流/超时最多 2 次尝试，每次计入请求上限 |
| 预算 | 连通性 1 请求 / 5000 Token / 0.01 USD；Single 默认 4 / 100000 / 0.05 USD；Multi 默认 12 / 100000 / 0.20 USD |
| 记录 | 实际 usage 的输入/输出 Token、延迟、按峰值标价估算费用；缺少 usage 报错 |

对固定的 LiteLLM `1.71.1`，已检查显式 DeepSeek Provider 能识别 `deepseek-flash`；裸模型名的自动 Provider 推断失败，因此代码不使用裸推断。根据 [DeepSeek 官方 V4.1 发布说明](https://api-docs.deepseek.com/news/news260910/)、[价格/模型文档](https://api-docs.deepseek.com/quick_start/pricing/)、[思考模式文档](https://api-docs.deepseek.com/guides/thinking_mode/) 和 [Chat Completion API](https://api-docs.deepseek.com/api/create-chat-completion/) 选择当前接口。思考模式会忽略 `temperature`，因此本版明确关闭思考模式；Tool Calling、JSON 输出和多轮历史已用假响应测试请求与解析结构，**尚未通过真实 API 确认模型行为**。预算是本地估算上限，不是服务端硬限额或最终账单。

## 安全缺陷修复

- `TaskAuthorizationPolicy` 只接受 Controller 登记、结构化可信任务中明确列出的精确写动作；核对 Agent、组织版本、工具、操作、资源、收件人字段和值、完整参数、阶段及证据 ID。可信来源本身不能授权任意写入。真实 ARE 写工具继续关闭。
- `MessageGateway` 在排队与投递时检查当前组织对象、版本、边、接收者可见范围、发送/接收/完全隔离；在途旧版本消息丢弃，共享记忆读写走相同检查。
- Worker 与 Verifier 由 Controller 分配已注册的只读工具白名单；普通 Agent 无修改权限的入口。Chain 的 Verifier 直达边、可见策略、实际消息投递都有测试。
- 运行时将提交给模型的实际消息、工具输出、Worker 摘要和 Verifier 包记录为派生来源。此记录是**可见输入的保守依赖**，不表示模型内部的精确因果或语义判断。
- 写入前持久化 `started` 指纹，成功后持久化 `completed`；异常保留 pending，重启后拒绝模糊重试。**尚无远端状态对账和跨进程并发锁**，不能把它作为生产事务保证。
- 动态团队的风险检测目前是有限的字符串规则。命中后隔离受影响 Worker，阻断其结果向协调者/Verifier 传播，但未实现完整语义攻击识别。

## ARE 与 Multi-Agent 状态

复用的 ARE 接口为 `JsonScenarioImporter`、`Scenario.initialize`、`ScenarioRunner`、`AgentBuilder` 和官方工具 Adapter。离线探测在一个固定场景中初始化 12 个 App、103 个工具，运行官方环境事件循环和 Validator，并导出 `results/are_offline_probe/` 下的 JSON。该探测**没有 Agent 模型**；即使官方离线结果显示 `success=true`，也不等于完成合法任务。

`real_are_single` 调用固定 DeepSeek 路由、接收官方任务、执行允许的 ARE 只读工具，并保留 Runner 轨迹和统计。由于没有 API Key 和费用确认，尚无真实模型、工具调用或成功条件的端到端结果。写工具仍关闭，需要写操作的任务无法据此完成。

`real_are_multi` 在相同 Scenario 和官方 Runner 中增加 `delegate_parallel_reads`。实际触发后，协调者给两个 Worker 发子任务，Worker 以自身白名单调用模型和 ARE 只读工具，消息经网关，Controller 依据观察到的工具调用做 Star→Chain 或风险重组，Verifier 通过授权边接收证据并回复。离线假响应测试覆盖风险和普通路径。**它是嵌入工具调用的多模型角色，不是 ARE 原生多 Agent Scheduler；没有真实在线触发记录。** `dynamic_team_executed` 仅在真实调用造成组织版本变化后为真。MANTA 实际复用 `TopologySpec`、`ContextPolicy`、`TopologyMutation`；没有引入其 Engine、Planner 或 Trace Auditor。

## 验证与执行命令

已在当前 D 盘环境执行：

```powershell
Set-Location D:\SecurityAdaptiveMAS
.\bootstrap.ps1 -PythonExe .\python\python.exe -SkipDownloads
# Five pinned scenario checksums OK; 22 passed, 1 upstream Pydantic warning
```

另在 D 盘独立 `work/clean_venv_v2` 中安装兼容依赖，最新代码同样 **22 passed**。`bootstrap.ps1` 的 PowerShell 语法检查、Python `compileall`、`git diff --check` 通过；将 `UV_CACHE_DIR` 设在 D 盘后，`uv pip check` 显示 **98 个包兼容**。单独运行 `uv pip check` 时，默认 C 盘缓存目录因 ACL 拒绝访问；启动脚本已明确把缓存设在 D 盘。`deepseek_probe` 在缺少确认标志时正确拒绝调用；环境变量检查显示 `DEEPSEEK_API_KEY=UNSET`。离线 demo 输出 `mode=offline_mock` 和 `singleton → star → chain → chain`，不视为正式成绩。真实付费连通性命令如下，**本轮未执行**：

```powershell
Set-Location D:\SecurityAdaptiveMAS
$key = Read-Host 'DeepSeek API Key' -AsSecureString
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new('', $key).Password
.\.venv\Scripts\python.exe -m security_adaptive_mas.deepseek_probe --acknowledge-model-cost
```

该命令最多 1 次请求，超时 45 秒，最大 4096 输出 Token，预算 0.01 USD。后续 Single/Multi 命令见 `README.md`。截至本报告：`DEEPSEEK_API_KEY` 未设置；真实 API 调用 **0**，实际费用 **0 USD**。

## 尚未实现与下一阶段最短路径

1. 取得费用确认和环境变量后，仅运行上述单请求连通性检查；核对响应、usage、真实费用与固定路由。随后各执行一次只读 Single 和 Multi smoke，检查官方轨迹是否出现任务通知、ARE 工具结果回流及 `dynamic_team_executed`。不把只读 smoke 当正式任务完成。
2. 将真实 ARE 合法任务中的精确写动作映射为 Controller 签发的 `TaskAuthorization`；对不可回滚动作加入远端状态对账后才逐项开放写工具。这是合法任务成功评测的前提。
3. 从官方可见事件与轨迹提取未完成子任务、计划变化和工具状态；将阶段更新从当前工具调用触发扩展到长时程自动控制，并处理隔离后共享记忆与历史摘要的重新构造。
4. 在独立模拟环境实现低可信邮件/文件/消息/工具结果的 Attack Overlay 和四种策略统一入口，保持相同模型、预算与工具检查，分别记录合法目标和攻击目标，避免把网关收益全归因于拓扑。
