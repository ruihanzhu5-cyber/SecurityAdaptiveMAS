# Security-Aware Adaptive Multi-Agent Organization v2

固定五个 Gaia2 场景的安全组织控制原型。项目代码、Python 环境、ARE/MANTA 源码快照和场景文件位于 `D:\SecurityAdaptiveMAS`。`offline_mock` 可离线运行；2026-10-12 的单请求 DeepSeek 连通性检查已成功，但尚未运行真实 ARE Single/Multi 场景，不能宣称 Gaia2 任务完成或动态团队在线验证成功。详细证据与限制见 `implementation_report_v2.md`。

## 固定版本与安装

需要 PowerShell、Git、Python 3.12、`uv` 和网络。全新检出的项目可运行：

```powershell
Set-Location D:\SecurityAdaptiveMAS
.\bootstrap.ps1 -PythonExe .\python\python.exe
```

脚本把依赖环境和缓存建在 D 盘，检出配置中固定的 ARE/MANTA 版本，下载三个固定 Gaia2 Parquet 和固定文件系统数据，校验 SHA-256，恢复五个原始场景，再运行离线测试。请预留约 1 GB 下载和数 GB 磁盘空间。当前目录中的 ARE/MANTA 是归档源码快照，没有 `.git` 元数据；上述完整模式会明确报错，避免将快照误报为已验证的 Git checkout。在当前已迁移的 D 盘目录，使用现有源码和场景文件运行：

```powershell
Set-Location D:\SecurityAdaptiveMAS
.\bootstrap.ps1 -PythonExe .\python\python.exe -SkipDownloads
```

`-SkipDownloads` 核对现有五个 JSON 的哈希并运行测试，不证明第三方源码的 Git 身份。若要验证从零下载，请在新的干净项目检出目录执行完整模式。场景清单是 `configs/five_manifest.json`；版本与 Parquet 哈希是 `configs/source_versions.json`。当前已固定文件系统数据在 `work/gaia2_filesystem`，第三方源码未修改。

## 三种运行模式

### 1. `offline_mock`

```powershell
Set-Location D:\SecurityAdaptiveMAS
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests
.\.venv\Scripts\python.exe -m security_adaptive_mas.demo --scenario scenario_universe_22_0421h2 --trace results\demo.jsonl
```

这个模式验证控制器、拓扑、来源和网关。日志明确标记 `offline_mock`，不是 Gaia2 正式成功率。

### 2. `real_are_single`

先在当前 PowerShell 会话设置 `DEEPSEEK_API_KEY`，不要把密钥写入脚本或 Git。下面命令会产生真实费用；只在确认费用后执行：

```powershell
Set-Location D:\SecurityAdaptiveMAS
$key = Read-Host 'DeepSeek API Key' -AsSecureString
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new('', $key).Password
.\.venv\Scripts\python.exe -m security_adaptive_mas.are_smoke --scenario scenario_universe_22_0421h2 --allow-read-app Files --max-requests 4 --max-total-tokens 100000 --max-cost-usd 0.05 --acknowledge-model-cost
```

入口使用官方 `JsonScenarioImporter`、`ScenarioRunner`、事件模拟、Validator 与轨迹导出。默认仅开放 Files 的官方只读工具；ARE 任务若需要写操作，正式目标不能在此入口完成。输出 `success` 是官方 Runner 的验证结果，需结合 `error_type`、轨迹及是否实际执行工具解读。

### 3. `real_are_multi`

同一 ARE Runner 中安装 `delegate_parallel_reads` 工具。协调者调用此工具时，两个低权限 Worker 分别调用 DeepSeek 和授权的 ARE 只读工具，经 `MessageGateway` 返回；控制器重构组织并将可见证据交给 Verifier。没有在线端到端验证，不保证模型会主动调用此工具。命令同样只在确认费用后执行：

```powershell
Set-Location D:\SecurityAdaptiveMAS
.\.venv\Scripts\python.exe -m security_adaptive_mas.are_multi --scenario scenario_universe_22_0421h2 --allow-read-app Files --max-requests 12 --max-total-tokens 100000 --max-cost-usd 0.20 --acknowledge-model-cost
```

请检查输出的 `dynamic_team_executed`、`organization_version`、官方轨迹和 JSONL 组织日志。`dynamic_team_executed=false` 表示本次没有实际执行动态团队。

## 最小连通性检查

以下单请求检查已在 2026-10-12 获得费用确认后执行成功。再次运行仍会产生一次新的付费请求；它不运行 Gaia2 任务：

```powershell
Set-Location D:\SecurityAdaptiveMAS
.\.venv\Scripts\python.exe -m security_adaptive_mas.deepseek_probe --acknowledge-model-cost
```

该命令最多发送 1 个请求、最多 5000 个总 Token，按当前峰值标价的费用预算为 0.01 美元。本次返回 `OK`，输入 11 Token、输出 1 Token，本地费用估算为 0.0000045 美元。ARE 单 Agent 默认最多 4 个请求、预算 0.05 美元；动态团队默认最多 12 个请求、预算 0.20 美元。费用统计是基于 API usage 的估算，不是账单；模型返回没有 usage 时会报错。超时 45 秒，仅限流或超时最多重试一次，重试计入请求上限。所有入口固定 `deepseek-flash`、官方 DeepSeek endpoint，不回退其他 Provider。

## 当前边界

- 动态团队 Worker 和 Verifier 是嵌在同一个官方 ARE Agent 工具调用中的模型角色，共用一个 Scenario；不是 ARE 原生多 Agent Scheduler。子任务识别来自协调者实际调用，尚未自动从全部轨迹或事件提取。
- 安全监测目前是有限的确定性字符串规则。真实语义攻击判断、跨阶段共享状态清理和四策略攻击对照尚未完成。
- 写工具在真实 ARE 入口关闭。精确任务授权和持久化写日志已在控制层实现并通过离线测试，但真实 Gaia2 写操作尚未接线，也没有跨进程并发事务保证。
- 当前目录的第三方源码是固定版本的归档快照；完整 bootstrap 需要干净检出以完成 Git 版本核验。
