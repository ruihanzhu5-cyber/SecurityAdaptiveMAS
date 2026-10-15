# v0.6 固定源码、实际复用与许可

日期：2026-10-15。机器可核对的完整记录为 `configs/upstream_v0_6.lock.json`：逐文件记录固定提交、Git blob SHA、原始字节 SHA-256、源码绝对路径、派生路径/哈希及修改原因。

| 上游 | 固定提交 | 本版使用 |
|---|---|---|
| SecurityAdaptiveMAS | `56a30e320eb1017e6fa5f2e66320977c3e6e2a97` | v0.5 基础 |
| [MANTA](https://github.com/mao-code/MANTA/tree/3da40879d690a25459cc401292e5c6f0cc366d84) | `3da40879d690a25459cc401292e5c6f0cc366d84` | 私有最小源码子集 |
| [AOrchestra](https://github.com/FoundationAgents/AOrchestra/tree/14a1a2051d6b03c479b706f8f555a60b8419e3b5) | `14a1a2051d6b03c479b706f8f555a60b8419e3b5` | 派生 ReActAgent/Memory |
| [ATM](https://github.com/sidikbro/jiuwen_atm/tree/951e9aa8c8421e8b8d59302ee4727cc855f84714) | `951e9aa8c8421e8b8d59302ee4727cc855f84714` | 显式本地源码依赖 |
| [ARE](https://github.com/facebookresearch/meta-agents-research-environments/tree/a9fd2edccb4bc7e22fbf45c8482a5fef30a2a5a4) | `a9fd2edccb4bc7e22fbf45c8482a5fef30a2a5a4` | 官方 Runner、Coordinator、Tool 与日志 |

## AOrchestra 的真实调用

私有包保留 `aorchestra/subagents/react_agent.py`、`base/agent/{base_agent,base_action,memory}.py` 和 `benchmark/common/env.py` 的对应结构。

WorkerRunner 调用 `reset(BasicInfo)`、逐步 `await step(...)`，上游步骤仍调用 `_build_prompt`、`Memory.as_text` 和 `Memory.add_memory`。`ObservedReActAgent._build_prompt` 在调用 super 后捕获该次实际组件；模型经 BudgetedWorkerLLM 进入已有 DeepSeekClient/RunBudget。

明确派生修改：私有 import；注入 callable 类型并拒绝缺失；去除吞掉模型异常的空字符串；动作只解析一次严格 JSON；精确工具名；空工具列表不扩权；去除 ExecuteCodeAction 提示；最后一轮才强制 finish；普通 logging。Memory 上限为最大步数加一，首版不会触发额外模型压缩。

不使用原 DelegateTaskTool、模型工厂、Gemini 总结、Benchmark Runner、修 JSON/no_action 兜底或异常 cost=0。

原始字节副本位于私有包的 `originals/`，逐文件派生 diff 位于 `modifications/`。Apache LICENSE 原文保留；该归档未找到额外 NOTICE 文件，不补造通知。

## MANTA 的真实调用

复制范围：`answer_utils.py`、`MAS/artifacts.py`、`MAS/relay.py`、`MAS/self_evolved/{spec,context}.py` 和 LICENSE。包初始化为空，不复制会引入全栈的 MAS 初始化。只修改 answer_utils 的私有 import。

适配器构造 AgentNode/ContextPolicy/GroupSpec/TopologySpec，调用 spec.validate 和 `SharedContextController.visible_packets(..., latest_only=False)`。返回的实际选包结果决定是否交付；若包未选中或内容与 Evidence 不一致，明确失败，不绕过筛选。

MANTA star group 仅为容器视图。生效图仍是本项目的有向 RuntimeGraph；不调用 MANTA mutation/to_layout/evidence_digest，不启用 summary_only 或隐藏截断。MIT LICENSE 原文与修改记录保留。

## ATM 的真实调用

从显式 `atm_root` 正常 import，核对每个文件哈希与 module.__file__。实际调用 CapabilityPolicy.register、MutationContract.verify_invariants 和继承的 StateDistiller.distill。

ExactStateDistiller 只重载精确去向函数；对象 ID/版本 Counter、父空集合、deny、forbidden 和 child scope 在本项目补足。候选选中后提交路径消费已验证 destinations。shadow_completed 保持 false，不声称运行了影子任务或撤销工具副作用。

**许可 OPEN：**固定提交的 README/pyproject 声明 MIT，但根 LICENSE 不存在。本版没有复制 ATM 代码到发布包，仅依赖本地固定源码。打包再分发前需要澄清许可文本缺口，不能把本地可 import 当成再分发材料齐全。

## 本机配置与复现

```yaml
# work/v0_6_local_paths.yaml，被 Git 忽略
atm_root: D:/SecurityAdaptiveMAS-v0.6/work/upstream/atm
are_root: D:/SecurityAdaptiveMAS/work/facebookresearch-meta-agents-research-environments-a9fd2ed
gaia2_filesystem: D:/SecurityAdaptiveMAS/work/gaia2_filesystem/demo_filesystem
scenario_root: D:/SecurityAdaptiveMAS
sandbox_root: D:/SecurityAdaptiveMAS-v0.6/work/are_tmp
```

scenario_root 是固定场景 manifest 路径的基目录；新增此显式路径是为读取旧目录数据而保持 SECURITY_MAS_ROOT 指向新 checkout。没有同名目录搜索或自动替代。

`scripts/fetch_v0_6_sources.py` 获取固定 AOrchestra/ATM 归档，保存归档 SHA-256；`scripts/vendor_v0_6.py` 校验任务书提供的 blob，再生成私有子集、原始副本、diff 和锁。路径变更需显式调整并重新核验锁，不把父仓库 HEAD 当上游版本。

新 `.venv` 由 D 盘 Python 3.12.13 创建，include-system-site-packages=false。首次网络安装缓慢，已取消该安装进程；随后从已有 wheel 缓存重新安装独立依赖，不复制旧环境。ARE 使用显式固定源码路径；AOrchestra/MANTA 整份 requirements 未安装。

实际依赖版本列表保存在本地 `work/cached_runtime_versions.txt`；项目通过 editable 安装注册 `security-mas`。没有复制旧环境中的模型密钥；运行时只检查环境变量是否存在。
