# D 盘统一目录

目标目录：`D:\SecurityAdaptiveMAS`。

```text
D:\SecurityAdaptiveMAS\
  .git\                 本项目 Git 仓库
  .venv\                Python 3.12 隔离环境
  python\               本地 Python 3.12 运行时
  configs\              固定五场景配置
  security_adaptive_mas\ 本项目代码
  tests\                离线测试
  work\selected\        五个原始场景 JSON 与 manifest
  work\selected_single\ ARE 单场景加载目录
  work\facebookresearch-meta-agents-research-environments-a9fd2ed\ ARE 官方源码
  work\mao-code-MANTA-3da4087\ MANTA 官方源码
  archive\c_workspace_2026-10-11\ 旧 C 盘工作区完整归档（含原始数据与筛选过程）
```

Git 只跟踪本项目源码、配置、测试和文档。`.venv`、Python 运行时、第三方源码、原始场景数据、旧工作区归档以及缓存由 `.gitignore` 排除。两个官方仓库的版本和许可记录在 [v1 实施报告](implementation/implementation_report_v1.md)。旧 C 盘工作区已完整归档至 `archive\c_workspace_2026-10-11\`；其中 `work\data\` 含 3 个全量原始数据文件。

2026-10-12 已完成复制、Python 3.12 虚拟环境安装、ARE 官方依赖安装、五场景官方 JSON 导入器检查和 9 个离线测试。`uv pip check` 显示 98 个包兼容。Git 仓库位于本目录 `.git\`，分支为 `main`；没有配置远端，也没有推送。旧工作区归档与原目录逐文件 SHA-256 校验：5,699 个文件、712,540,571 字节、0 处差异。校验通过后已清空原 C 盘目录中的所有文件；空目录因当前 Codex 会话占用，待会话释放后才能删除。

旧版迁移脚本仅作为历史记录保存在归档中；当前 D 盘环境已经安装完毕。需要验证时，在本目录运行 `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests`。真实 ARE 场景回合和动态多 Agent 模型执行尚未运行，详见 [v1 实施报告](implementation/implementation_report_v1.md)。
