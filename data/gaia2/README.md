# Gaia2 数据放置与使用

本目录保存已用于历史实验的五个原始场景 JSON。完整 Gaia2 数据保存在本仓库的 [Gaia2 数据 Release](https://github.com/ruihanzhu5-cyber/SecurityAdaptiveMAS/releases/tag/gaia2-data-78ea3bd)：7 个分类的原始 Parquet，以及 ARE 运行所需的文件系统快照。大文件不进入 Git 历史，克隆代码时无需自动下载约 1.2 GB 的附件。

## 数据来源与版本

- 场景数据：Meta AI Research Team 的 [Gaia2](https://huggingface.co/datasets/meta-agents-research-environments/gaia2/tree/78ea3bdbdeec2bdcd6afa5420915d8a22f23ed99)，固定 revision `78ea3bdbdeec2bdcd6afa5420915d8a22f23ed99`。
- 文件系统：[Gaia2 Filesystem](https://huggingface.co/datasets/meta-agents-research-environments/gaia2_filesystem/tree/132e26376f5e963bb59f64bcccdd02188cb08dee)，固定 revision `132e26376f5e963bb59f64bcccdd02188cb08dee`。Release 中的 ZIP 仅打包其 `demo_filesystem` 目录，没有改动文件内容。
- 两个上游数据集的页面均标注 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。数据归 Meta AI Research Team / Meta Platforms, Inc. 所有；这里的镜像与五例提取不代表上游认可本项目。
- 文件大小与 SHA-256 记录在 [`configs/source_versions.json`](../../configs/source_versions.json)。五例 JSON 的哈希与原始行号记录在 [`configs/five_manifest.json`](../../configs/five_manifest.json)。

## 目录关系

```text
data/gaia2/selected/             Git 中的五个固定开发场景；克隆即可核对
configs/five_manifest.json       五例的 ID、分类、行号、JSON 哈希
configs/source_versions.json     官方 revision、Release 标签、原始文件哈希
work/data/                      下载后的 7 个 Parquet 和文件系统 ZIP；不进 Git
work/gaia2_filesystem/          解压后的 ARE 文件系统；不进 Git
work/full_scenarios/            按需导出的其他场景 JSON；不进 Git
```

## 下载、查看和选择

以下命令在仓库根目录运行。下载需要 GitHub CLI `gh`；查看和导出需要项目 Python 环境中的 `pyarrow`。

```powershell
# 从本仓库 Release 下载原始文件，逐个校验 SHA-256，再解压文件系统
.\.venv\Scripts\python.exe -m security_adaptive_mas.dataset_store fetch

# 查看 execution 分类前 20 个场景 ID；总数同时输出
.\.venv\Scripts\python.exe -m security_adaptive_mas.dataset_store list --category execution

# 从固定 Parquet 原样导出一个场景，不修改任务、事件或官方答案
.\.venv\Scripts\python.exe -m security_adaptive_mas.dataset_store export --category execution --scenario scenario_universe_22_kb5ah9

# 只检查完整数据入口，不调用付费模型
.\.venv\Scripts\python.exe -m security_adaptive_mas.run_framework --mode adaptive --dataset-category execution --scenario scenario_universe_22_kb5ah9 --dry-run
```

`--suite five` 和不带 `--dataset-category` 的五例运行仍使用固定开发集。增加 `--dataset-category` 后，单场运行从对应的原始 Parquet 选取 ID，记录为尚未审查的 Gaia2 validation 场景；这不自动变成 held-out 测试集。真实运行仍需预算参数、模型密钥和 `--acknowledge-model-cost`。本次搬运数据没有执行新的 DeepSeek 实验。
