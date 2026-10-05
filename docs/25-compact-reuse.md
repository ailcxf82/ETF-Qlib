# 轻量结果复用与共享基线

实验完成后，研究结论独立保存为结果包。研究记忆和重复因子准入读取结果包，原 run 转入冷归档后仍可查询。完整重放和新增指标计算需要恢复相应原始材料。

## 存储

活动运行固定使用本地 `artifact_root/reuse`。运行产物和复用缓存保留在项目所在磁盘；可拔出的 F 盘只存冷归档和离线导出的便携结果包。活动命令不会读取或写入 F 盘，因此拔出后新运行和本地复用继续工作。

| 子目录 | 内容与能力 |
| --- | --- |
| `evaluations/<id>` | 已提交 trial、研究卡片、配对评估 JSON、冻结协议、因子源代码、哈希清单；支持历史结论和查重 |
| `runs/<id>` | 原运行身份、配置、状态、报告、清单和结果包引用 |
| `baselines/<id>` | 共享基线预测、收益、交易/持仓诊断、模型；不复制输入特征矩阵 |
| `index.json` | 可由结果包独立重建的索引 |

导出时先验证原始 checkpoint、trial、报告、子运行和模型。之后只检查独立结果包。旧证据缺失的试验仅保存历史反馈，不升级为已验证结论。SHA-256 检查本地内容完整性，不是外部签名或数值正确性的独立证明。

包内部使用相对路径；保存的原绝对路径只作来源记录。`.staging` 半成品不参与查询。

## 运行行为

- checkpoint 提交后尝试导出；失败记录 `reuse_export_pending`，保留原结果供重试。
- 已导出卡片避免重复检查原模型/子运行。仅由结果包组成的记忆索引支持直接缓存读取；未迁移来源继续走旧校验。
- LLM 提出因子后、编码和训练前查重。本次提案费用如实计入；显式查询命令不调用 LLM。
- 首轮独立基线计算延迟到准入后；结果包命中可直接完成首轮，记录复用来源。
- 配对基线按快照、特征集、模型/seed、成本、策略和计算环境共享，同条件只计算一次。
- 准入绑定因子定义、快照、基线、协议及已尝试次数。新协议携带计算代码指纹，CLI 和存储/索引调整不单独改变它。旧协议缺少该指纹时只按旧协议身份查历史，不追认与新实现兼容。

`accepted` 仍表示原研究结论。结果包没有晋升因子所需的特征矩阵，标记 `promotion_materialized=false`；加入累积基线须按保存实现重新物化并通过现有特征发布校验。独立验收和投资资格不会因缓存命中升级。

## 命令

在项目根目录执行，使用 `E:\tools\anaconda\envs\qlib_zhengshi\python.exe`。

```powershell
python -m etf_ml.cli export-reuse --config configs/data/tushare_formal_first_loop.yaml --source-run artifacts/runs/<run-id>
python -m etf_ml.cli query-reuse --definition-id <definition-id>
python -m etf_ml.cli query-reuse --definition-id <definition-id> --snapshot-id <snapshot-id> --baseline-id <baseline-id> --protocol-id <protocol-id> --attempted-trials 1
python -m etf_ml.cli rebuild-reuse-index
python -m etf_ml.cli preview-run-cleanup --config configs/data/tushare_formal_first_loop.yaml --source-run artifacts/runs/<run-id>
```

仅 definition ID 查询历史；提供上下文返回准入决策。定义 ID 可从导出包 `result.json` 的 `card.definition_id` 获取。连接 F 盘时，导出、查询或重建离线归档索引可显式传 `--reuse-root F:/ETF/ETF-Qlib/reuse`。

启动 `first-loop`、`research-factor` 或单独 `baseline` 时使用本地 `artifact_root/reuse`。这些运行命令会忽略旧配置或命令行中指向可移动磁盘的 `reuse_root`，并提示便携库只用于离线操作。离线的 `export-reuse`、`query-reuse`、`rebuild-reuse-index` 和 `preview-run-cleanup` 仍支持显式 `--reuse-root`；`rebuild-reuse-index` 同时重建结果包索引和共享基线索引。

需要把便携结果包作为本地复用数据时，先连接 F 盘，将其 `reuse` 目录合并到本地 `artifact_root/reuse`，然后在本地重建索引。复制完成后即可拔出 F 盘；运行只访问本地库。请勿把活动 `reuse_root` 或 `artifacts/runs` 目录联接到 F 盘。

## 清理与恢复

`artifacts/runs` 是 D 盘的活动根目录。已结束的原始运行可归档到 F 盘；D 盘清理后该根目录仍保留为空，后续新运行会在 D 盘创建。F 盘拔出时，已归档运行的原始审计、重放和额外归因不可用，需重新连接 F 并将对应 run-id 目录恢复到本地。轻量结果包和共享基线放在本地 `artifact_root/reuse`，不要求原始运行目录常驻。

清理预览命令仍只列候选、不删除文件。保留状态、checkpoint/trial、campaign/费用账本、注册库及已发布特征集；工作空间、配对运行、执行目录和独立基线可进入归档审查。存在进行中 checkpoint、导出缺失或引用依赖时先解决对应问题。

引用检查覆盖同级运行的摘要和基线指针，外部脚本、自定义消费者和完整审计仍需人工检查；预览不会把路径标为可无条件删除。

原 `RunStore` 全量复放仍依赖旧 manifest；清理后使用本地轻量查询/准入。归档恢复示例：

```powershell
robocopy F:\ETF\ETF-Qlib\artifacts\runs\<run-id> D:\quant_project\ETF-Qlib\artifacts\runs\<run-id> /E /COPY:DAT /DCOPY:DAT /R:2 /W:2
```

原 snapshot 和 shared baseline 有独立消费者，不随一个 run 一并清理。F 盘为 FAT32，单文件不能超过 4 GiB。

## 验证

`tests/unit/test_compact_reuse.py` 覆盖原目录移走、包迁移、索引重建、包损坏、checkpoint 篡改、身份变化、结论冲突、并发导出、零训练复用及共享基线引用。

`tests/integration/test_compact_baseline.py` 使用本地合成数据验证真实 Qlib 输出：移动原目录和复用库后跳过基线训练，以及两个配对实验目录共享相同基线，保持预测、收益和配对统计一致。该测试不覆盖 Docker 因子执行。

2026-09-30 抽样导出 `rdagent-mechanism-20260928-r05`：原目录 1,609,758,613 字节，结果包和运行凭据共 32,368,842 字节，约为原体积的 2.01%；三次同协议查询中位耗时约 185 ms。旧协议仅按原身份复用，不自动判定与当前计算代码兼容。原文件未删除，因此这不是已释放空间。

验证用便携结果库位于 `F:/ETF/ETF-Qlib/reuse`；运行时需将需要的包并入本地 `artifact_root/reuse`。活动结果留在 D 盘，F 盘只保存冷归档和离线便携包。Docker 端到端回归已通过配对实验和 RDAgent 工作流。
