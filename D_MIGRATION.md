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
```

Git 只跟踪本项目源码、配置、测试和文档。`.venv`、Python 运行时、第三方源码、原始场景数据以及缓存由 `.gitignore` 排除。两个官方仓库的版本和许可记录在 `implementation_report_v1.md`。原 C 盘工作区会保留作为备份，不执行移动或删除。

在原项目目录运行 `./setup_d.ps1` 会复制这些文件，初始化 Git，创建 D 盘虚拟环境，安装离线依赖并运行测试。默认还尝试安装 ARE 官方依赖并验证官方 JSON 导入器；如网络下载失败，离线骨架仍已就绪，可以重新执行脚本。当前脚本不调用付费模型 API。
