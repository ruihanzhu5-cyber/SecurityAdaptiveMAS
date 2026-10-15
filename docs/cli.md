# CLI 使用说明（v0.5）

本文参数对应实际 CLI。命令在 Windows PowerShell 仓库根目录执行；先安装并激活项目环境。`python -m security_adaptive_mas` 与 `security-mas` 同入口。

## 免费的本地检查

```powershell
security-mas --help
security-mas doctor --config configs/research_v0_4.yaml
security-mas run --help
security-mas run --design-config configs/research_v0_4.yaml --mode adaptive --scenario scenario_universe_22_kb5ah9 --dry-run
python -m security_adaptive_mas.run_framework --design-config configs/research_v0_4.yaml --mode adaptive --scenario scenario_universe_22_kb5ah9 --dry-run
```

doctor 只读检查包、配置、数据、源码目录和 key 是否存在，只显示布尔值，不加载 `.env` 或打印 key。目录存在不等于全部 ARE/AgentDyn 依赖可导入；AgentDyn 仍 BLOCKED。dry-run 会核对场景 ID、配置及选中 JSON 哈希，不启动环境、不调用模型、不创建运行目录。

真实运行依赖清单见[开发指南](development.md)。新旧入口的核心输出一致，包含任务、方法、模型、单场预算与批次预算。新入口不接受和 design-config 冲突的旧策略参数。

## 实验计划（不会执行）

```powershell
security-mas experiment plan --design-config configs/research_v0_4.yaml --output results/plans/development.json
security-mas experiment plan --design-config configs/research_v0_4.yaml --split frozen_evaluation --output results/plans/evaluation.json
```

默认 140 条开发计划；冻结评测 280 条，均为 `NOT_RUN`。每条明确任务/分类/哈希、方法、seed、模型和基础配置。计划还保存代码快照、配置文件和清单哈希；任何代码/工作区变化后应重新生成计划。文件不覆盖。四家族攻击尚未验证，因此自动矩阵当前只支持 `attack=none`，不会忽略攻击参数或冒充攻击集。

## 付费执行入口（本轮没有运行）

以下是未来明确授权后可使用的命令，不是本轮实验记录：

```powershell
$env:DEEPSEEK_API_KEY = '<在本机设置，不提交到 Git>'
security-mas experiment run --plan results/plans/development.json --limit 1 --max-total-cost-usd 1 --max-cost-usd 1 --acknowledge-model-cost
```

必须明确 `--limit`、`--max-total-cost-usd` 和 `--acknowledge-model-cost`。默认单场 20 turns / 100 requests / 800000 tokens / 1 USD，可通过同名参数调低，沿用旧运行器上限。总上限不超过 5 USD。输出目录默认 `results/v0_5_matrix`，可传 `--output-dir`；目录内既有账本费用也计入总上限。

每场开始前先预留整场上限，不足则 `BLOCKED_TOTAL_BUDGET`，不会偷偷缩小单场预算或再运行下一场。模型请求、未知计费上界与预算停止仍由旧 RunBudget 和账本记录。失败保存状态并重新抛出，不换 mock 或 KEEP。多个进程共享同一输出账本不受支持，请顺序运行，不并发启动同目录。

### 单场邮件入口

```powershell
security-mas run --design-config configs/research_v0_4.yaml --mode adaptive --scenario scenario_universe_22_kb5ah9 --attack single_injection --task-scope-policy sandbox_permissive --max-cost-usd 0.4 --max-requests 60 --acknowledge-model-cost
```

此命令会付费，**本轮未执行**。可以追加 `--dry-run` 做免费参数检查。复用原有已核验邮件返回字段，不新增攻击载荷或家族；敏感权限仍经过同一网关。

## 读取历史结果

```powershell
security-mas report --runs results/v0_3_pilot --output results/v0_5_pilot_report.json
python scripts/summarize_results.py --runs results/v0_3_pilot --output results/v0_5_pilot_report_script.json
```

`--runs` 接受单个 summary.json、单场目录或包含真实运行的父目录；没有 summary 就报错。输出不得覆盖已有文件，不修改原始数据。旧版缺少 evaluation.json 等文件会列为 absent，缺字段保持 unknown。旧邮箱评分字符串 true/false 仅作明确转换，同时保留原始 scoring。官方 success 不提升为合法任务完成。

## OPEN / BLOCKED

真实 v0.5 DeepSeek 端到端执行尚未授权。AgentDyn、并发、完整恢复和通用语义完成验收未支持。矩阵不支持未核验攻击或其他 benchmark 时明确报 UNSUPPORTED。详见[实施报告](reports/v0_5_implementation_report.md)。
