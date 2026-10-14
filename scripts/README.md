# 实验与维护脚本（v0.5）

先在仓库根目录安装项目环境，Windows PowerShell 可直接用 `python scripts/...`。三个实验脚本只转发到统一 CLI，不另写调度、选图、预算或评分。

| 脚本 | 输入 / 输出 | 模型费用 |
|---|---|---|
| [run_dev.py](run_dev.py) | 与 run 相同的参数；真实运行输出沿用原目录 | dry-run 无；真实运行有，必须明确确认 |
| [run_matrix.py](run_matrix.py) | plan 生成 NOT_RUN 清单；run 接受计划和限额 | plan 无；run 有，必须有限额与费用确认 |
| [summarize_results.py](summarize_results.py) | 读取 summary/trace，输出新 JSON | 无 |
| [check_docs.py](check_docs.py) | 检查 Git 跟踪及新建非忽略 Markdown 的本地文件/图片链接 | 无 |

```powershell
python scripts/run_dev.py --design-config configs/research_v0_4.yaml --mode adaptive --scenario scenario_universe_22_kb5ah9 --dry-run
python scripts/run_matrix.py plan --design-config configs/research_v0_4.yaml --output results/plans/development_script.json
python scripts/summarize_results.py --runs results/v0_3_pilot --output results/v0_5_pilot_report_script.json
python scripts/check_docs.py
```

结果目录是本机历史目录示例，不随代码克隆分发；源文件不存在时会报错，不生成模拟报告。输出文件不能覆盖。链接检查跳过 URL、页内锚点及代码块，检查实际本地文件与图片；复杂标题锚点未做自动验收。

付费批次的完整参数见[命令手册](../docs/cli.md)。本轮没有执行收费命令、未核验全部攻击家族；开发环境和 CI 覆盖边界见[开发指南](../docs/development.md)。
