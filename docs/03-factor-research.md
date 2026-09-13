# 03 RDAgent 因子研发规范

## 1. 因子研发目标

RDAgent 要寻找的是“能改善既定预测任务和组合政策的新增特征”。单因子 IC 是诊断指标，最终选择依据是固定机器学习系统中的条件增量、稳定性和交易成本。一个单独 IC 较弱但提供非线性交互信息的因子，不应仅凭低 IC 被一票否决。

Agent 不直接决定 ETF 买卖，不独立修改目标标签、训练区间、评价指标、成本或持仓约束。它提交候选代码与理由，确定性的研究协议负责运行和门禁。

## 2. ResearchContext 与 FactorSpec

每次提案都绑定一份只读 `ResearchContext`：

| 信息 | 必填内容 |
| --- | --- |
| 预测任务 | h=5；t 收盘完成后决策，t+1 开盘执行；标签版本 |
| 资产与样本 | ETF 类型、历史池政策、跟踪分组、研发可见日期 |
| 输入数据 | 字段名、含义、单位、发布时间、更新频率、缺失模式 |
| 既有特征 | 特征名、窗口、公式、同类相关性及已观察失效情形 |
| 主模型 | 固定 LightGBM 配置及预处理策略 |
| 评价 | 开发折、评价指标、成本、入选规则与当前基线 ID |
| 执行约束 | 允许库、输出格式、历史窗口、CPU/内存/时间预算 |
| 反馈历史 | 开发阶段通过/拒绝原因、尝试次数；不含留出信息 |

`FactorSpec` 必含：`factor_id`、版本、经济假设、公式/算法、`required_fields`、`lookback`、`minimum_observations`、信息滞后、适用池、是否跨截面、方向解释、缺失规则、复杂度、与既有因子的预期差异、源码哈希及 ResearchContext 哈希。

第一版最大回看窗口建议 120 个交易日，每轮 1–3 个候选；最大总尝试次数、API 成本和执行时长在启动参数中明确，预算耗尽即结束当前研究并保存状态。

### 因子任务示例

```yaml
factor_id: volatility_adjusted_momentum_v1
hypothesis: 相似动量下，波动更低的 ETF 可能提供更稳定的后续收益排序
required_fields: [adj_close]
lookback: 61
minimum_observations: 61
available_at: after_daily_ingestion
formula: "return_20d / (std_daily_return_60d + epsilon)"
groupby: instrument
output: one_numeric_column
missing_policy: insufficient_history_is_missing
target_horizon: 5
```

这是用于验证研发链路的示例候选，不代表其有效性已经被证明。其公式、字段名要由适配器映射到实际可见输入，不能把语义配置直接当作可执行代码。

## 3. 分级执行和质量门禁

1. **规格检查**：字段存在、窗口足够、明确适用池与预测跨度，无标签/留出依赖。
2. **隔离调试**：在开发区间小样本生成合法索引的单列数值输出，记录异常、执行时长及资源。
3. **因果性检查**：比较完整研发输入与截至 T 的输入在 T 及之前的结果；在 T 之后扰动数据，历史输出不应改变。检查时保持 ETF 池和同日可见截面语义一致。
4. **分组检查**：改变代码排列或加入另一只 ETF 不应改变纯时序因子的既有值；跨截面因子则按声明的群组范围测试。
5. **质量检查**：唯一索引、有限数、预热后覆盖率、常数比例、异常尖峰和分类覆盖。
6. **去重与诊断**：源码/公式哈希去重，按日计算与已有因子的正负相关，检查时序稳定性、Rank IC 及衰减。
7. **模型增量实验**：使用固定学习协议进行配对比较，再测组合和交易成本。

初始质量门槛可设为预热后目标池日期平均覆盖率 ≥95%、无重复键、无无穷值；必须同时报告最差日期和各类别覆盖，不能用全样本均值隐藏局部缺失。阈值是工程起点，G0 根据字段覆盖冻结后不得逐候选放宽。

除恒等/确定性重复外，高相关只触发冗余标记和优先级下降，不直接证明没有条件增量。阈值应检查绝对相关，避免把完全反号因子当作新信息。因果性数值检查与代码检查互补，任何单个测试均不能证明绝对无泄漏。

## 4. 与机器学习框架一致的实验矩阵

| 实验 | 特征 | 模型 | 用途 |
| --- | --- | --- | --- |
| B0 | 固定动量规则 | 无学习模型 | 可解释策略对照 |
| B1 | `F_base` | Ridge | 线性基线 |
| B2 | `F_base` | 固定 LightGBM | 当前候选的主对照 |
| C1 | `F_base + f_new` | 与 B2 相同 LightGBM | 新因子条件增量 |
| C2 | 当前入库特征减去候选组 | 与 B2 相同 LightGBM | 入选后消融确认 |
| C3 | 固定入库特征 | XGBoost/其他 | 后续模型稳健性比较 |

配对实验必须相同：数据快照、代码池、目标标签、时间折、训练和评价样本规则、预处理、模型超参数、随机种子、调仓、账户、成本和执行近似。允许模型在相同 early_stop 规则下获得不同最佳轮数，需一并记录，不能私下增加候选调参预算。

主评分基于选择期的日组合结果，不用单因子分层收益直接替代模型组合。因子重要性、SHAP 或置换重要性用于解释，不能作为独立入库依据；高度相关时解释值本身也可能不稳定。

## 5. 选择规则与反馈

采用硬约束 + 可解释排序，避免把所有指标未经尺度处理相加成为任意总分。

### 初始候选入选规则

- 数据质量和无泄漏检查通过；绝对回撤和集中度上限在 G0 冻结。
- 对固定模型、固定政策，逐折扣费后超额收益增量的中位数为正，且多数折增量为正。
- 换手与最大回撤恶化不超过预先冻结的容忍范围；费用压力情景下仍满足配置的收益/风险下限。
- 至少使用预先固定的多个种子或重跑检查，证明不是单次训练异常；记录所有运行，不挑最好一次。
- 通过消融实验后进入候选库；预算不足或证据不稳定则标为观察，不自动宣称有效。

以上只构成开发阶段筛选标准，不是统计显著性或实盘盈利保证。具体收益、回撤与预算阈值属于配置项，在观察候选结果之前确定。若所有候选均无增量，应输出“没有更优候选”并保留固定基线。

结构化反馈包含：

```text
status: accepted | rejected | inconclusive | failed
baseline_id / candidate_id / protocol_id / run_id
data_quality: coverage, nonfinite, time_check, index_check
predictive_metrics: IC, RankIC, effective_dates, by_fold
portfolio_metrics: net_return, excess_return, drawdown, turnover, costs
paired_deltas: 各折/各随机种子的候选减基线结果
robustness: group_contribution, cost_stress, ablation
decision: 固定规则的结果与原因代码
research_notes: 供下一轮假设使用的开发期观察
```

反馈必须区分训练失败、数据失败和因子无增量。RDAgent LLM 可解释结果并提出下一轮方向，但不能推翻硬门禁。保留因子方向和适用类别信息，避免把某一行情段效果误表述成普适规律。

## 6. 适配本机 RDAgent 0.8.0

| 扩展点 | 拟实现类 | 必须替换的行为 |
| --- | --- | --- |
| `QLIB_FACTOR_SCEN` | `ETFFactorScenario` | 股票背景与字段说明改为 ResearchContext |
| `QLIB_FACTOR_HYPOTHESIS2EXPERIMENT` | `ETFHypothesis2Experiment` | 候选与 baseline 都使用 ETF Experiment/Workspace |
| `QLIB_FACTOR_CODER` | `ETFFactorCoder` | 因子实现、测试与环境探测使用统一隔离执行器 |
| `QLIB_FACTOR_RUNNER` | `ETFFactorRunner` | 因子校验、特征对齐、配对训练回测 |
| `QLIB_FACTOR_SUMMARIZER` | `ETFFeedback` | 读取结构化开发期指标并执行选择规则 |

模块位于 `etf_ml.adapters.rdagent`，实际环境变量指向对应完整模块类路径。配置必须在导入 RDAgent settings 单例之前注入；不得假定最新在线文档中的时间参数已在本机版本实现。

本机默认 Hypothesis2Experiment 显式创建 `QlibFactorExperiment`，后者固定载入包内股票模板；只修改 Scenario 或 provider URI 不会覆盖这个链路。本机默认 Qlib workspace 还根据模型配置选择执行环境，因此因子执行器与回测执行器都要改。

默认因子 Runner 存在组合后直接删除缺失行、按固定产物名保存、缓存复用等路径，适配器必须对样本一致性和数据版本负责。默认反馈按固定指标名提取，扩展 ETF 反馈时需提供完整映射，不能把缺失指标静默填 0。

本机数据链路采用 `daily_pv.h5`、单因子 `result.h5`、组合 `combined_factors_df.parquet`；生成文件要保留正确索引和 `feature` 列组。新的在线版本接口可能变化，兼容测试以锁定版本为准。[RDAgent 因子 Runner 参考](https://github.com/microsoft/RD-Agent/blob/main/rdagent/scenarios/qlib/developer/factor_runner.py)

`fin_quant` 放在因子链路稳定后接入，其模型路径和因子路径都使用同一 LabelSpec、FeatureSet、ValidationProtocol 和 PortfolioPolicy。交替优化时一次只改变一类变量，重新建立基线；不直接启用无限制的因子/模型共同搜索。

## 7. 因子注册与生命周期

状态为 `proposed → validated → evaluated → candidate → frozen`；失败进入 `rejected`，已冻结后发现数据或实现问题进入 `retired`，保留历史版本。

每个版本记录经济假设、输入字段、窗口与时间可用性、源码、测试结果、实验矩阵、适用范围、数据与协议版本、审批/冻结记录。改变公式、单位、缺失处理或字段时点都产生新版本，不能覆盖同名因子的历史定义。

最终模型只使用冻结的 feature_set manifest。日常推断重用同一份因子代码，避免研发一套、上线另一套。发现漂移或性能下降只触发诊断与新研究版本，不自动改写当前模型的因子。
