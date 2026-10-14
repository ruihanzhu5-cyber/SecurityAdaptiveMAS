# 开发与环境（v0.5）

本项目主要使用 Windows PowerShell。Python 元数据要求 `>=3.10`，来自已有 ARE 上游的要求和当前源码语法；本轮实际验收环境为 Python 3.12.13，尚未逐版本测试。

## 安装边界

在代码仓库根目录：

```powershell
python -m pip install -e .
security-mas doctor --config configs/research_v0_4.yaml
```

基础安装只安装 CLI 所需 PyYAML，不安装 MANTA 全部依赖。`[model]` 声明已有 LiteLLM 版本，`[are]` 声明本地上游元数据版本 1.2.0 和 LiteLLM，`[data]` 沿用已有数据依赖，`[dev]` 用于测试和检查。版本依据是现有 requirements、已安装元数据和固定 ARE 源码，不代表完成了全新环境兼容性验证。

已有虚拟环境与本机缓存可使用离线命令：

```powershell
uv pip install --offline --python .venv/Scripts/python.exe setuptools wheel
uv pip install --offline --no-deps --no-build-isolation --python .venv/Scripts/python.exe -e .
```

如果缓存不存在会直接失败；不要自动去外网下载。当前环境没有虚拟环境内的 pip，也可以用本机已有 pip：`python -m pip --python .venv/Scripts/python.exe install --no-index --no-build-isolation --no-deps -e .`。这是本轮实测的 editable 安装方式。

## 真实 ARE 路径还依赖什么

现有 [gaia2_v1_five.yaml](../configs/gaia2_v1_five.yaml) 保存 ARE/MANTA 本地源码位置。Controller 仍使用 MANTA 的小型拓扑内核；安装基础 CLI 不会下载这些源码。ARE 导入器还需要本机已安装的上游依赖，以及 `work/gaia2_filesystem/demo_filesystem`。五个选中 JSON 在 Git 中，完整 Parquet/文件系统在 Release。

[bootstrap.ps1](../bootstrap.ps1) 是原有配置脚本，可能联网下载源码/数据或安装依赖；本轮未执行。若已有全部下载，可审阅其 `-SkipDownloads` 路径。完整数据命令另见[数据说明](../data/gaia2/README.md)。从空目录到完整模型运行未在本轮验证，不把 CLI 安装成功等同全部环境就绪。

非 editable wheel 用户需要设置 `SECURITY_MAS_ROOT` 指向包含 configs、数据和 Git 元信息的实际 checkout；结果冻结依赖 Git，单独 wheel 不携带数据和上游源码。本轮已验证 editable，独立 wheel 的全链运行仍 OPEN。

API key 从进程环境读取；`.env.example` 仅是变量说明，CLI 不自动加载 .env。不要把 key 写入命令报告、日志、配置或提交。

## 检查

```powershell
python -m compileall -q security_adaptive_mas scripts
python -m pytest -q -p no:cacheprovider tests
python scripts/check_docs.py
```

全量测试需要已有本地 ARE/MANTA 源码和文件系统，其中包含真实只读工具检查但没有真实模型请求。CI 只跑可移植的 CLI/config/计划/预算拒绝/只读汇总测试，不假定完整数据和上游 checkout；不会用 mock 冒充完整 ARE 验收。

pre-commit 的 Ruff 只覆盖本轮新入口、scripts 和 v0.5 测试；基础 YAML/TOML/冲突标记检查按 hook 执行。没有对历史研究模块作全仓格式化。新安装 pre-commit/Ruff 可能联网，本轮只在本机已有缓存可用时离线安装。

## 当前仍开放

AgentDyn 工具目录、并发原子语义、完整恢复、独立内容/任务完成判据和真实成本校准仍 OPEN / BLOCKED。本轮不付费，不自动 commit/push。每次审查后的新研究动作单独决定，见[实施报告](reports/v0_5_implementation_report.md)。
