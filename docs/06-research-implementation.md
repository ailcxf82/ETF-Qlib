# 配对研究与因子注册实现

本文件说明新增的项目 API，不表示真实数据 G0–G4 已通过。CLI research-factor 已接入实际 RDAgent ETF 扩展类、可恢复多轮控制和受限原生训练/回测执行。已入选因子累积及候选组消融已接入；最终全组确认/冻结、模型比较与显式版本冻结、独立留出及每日影子链路已有工程实现；真实 LLM、真实独立投资验收及真实交易日影子证据仍待完成。

## 冻结协议与实际运行

- `etf_ml.research.protocol.ComparisonProtocol` 绑定快照、固定基线特征、horizon、历史池、所有开发折、主 LightGBM 参数、组合政策、辅助基线窗口、种子、费用压力、资源政策、代码和环境版本。
- `etf_ml.research.paired.combine_features` 只接受与协议一致、通过控制器质量检查的单列因子。核对缓存文件哈希和实际值，保留全部基线行，不通过 dropna 缩小候选样本。
- `etf_ml.research.paired.run_paired` 对所有冻结种子运行实际 Qlib 基线、候选及独立移除新增因子后的重训消融。每次训练复用原有 Dataset、模型包和 ETF 回测模块；费用压力使用同一预测，仅改变预先声明的佣金、最低费用及滑点倍率。
- 基线缓存独立于候选。候选缓存包含因子版本和结果数据哈希。重跑核验自身文件、所有引用实验及模型 manifest/权重文件；失败重试保留旧尝试，包括嵌套产物。

调用前先完成合格快照及参数口径冻结，在生成候选之前创建协议并把 protocol_id 写入 ResearchContext。协议的 stress_min_excess_return 默认为空；未明确压力收益下限时不允许自动入选。

集成测试使用合成行情、明确的测试口径和本机固定 Docker 镜像；其阈值不自动成为正式投资研究阈值。

## 确定性评价

`etf_ml.research.selection.compare` 核对全部折/种子、模型参数、train/early_stop/selection 样本哈希、处理器拟合索引、日组合评价日期及费用压力矩阵。

入选要求逐折种子中位增量为正且多数折为正，各种子跨折中位增量为正，回撤/换手恶化在冻结容忍内，绝对风险和压力下限满足政策，独立消融支持新增因子。缺少消融或压力阈值返回 inconclusive；缺少指标、协议或样本不一致返回 failed；无增量或违反门禁返回 rejected。LLM 不能改变此结论。

时间块诊断从匹配的组合日收益重采样，保留局部时间相关性，不把 ETF 行或模型种子当作独立时间样本。默认块长20、1000次、固定种子42；块长必须覆盖标签跨度。少于三个时间块明确给出证据不足；诊断不消除反复研发选择偏差。

## 不可变因子注册

`etf_ml.registry.FactorRegistry` 提供 register、load、transition：

- 同一 factor_id/version 只能绑定一份定义及 lineage；修改源码、公式、单位、缺失策略或上下文需新版本。
- 状态：proposed → validated → evaluated → candidate → frozen；失败可转 rejected，冻结后问题转 retired。
- validated 要求通过的质量证据；evaluated 要求结构化评价；candidate 必须使用已提交且 accepted 的同一评价；frozen 要求包含此版本的 feature_set。
- 证据绑定 factor_version_id，并复制到注册库。工作区证据后来改变不会改写历史决定；注册库源码、事件链或证据变更会被哈希验证发现。
- 定义原子发布，状态通过原子 head 引用不可变事件链；未发布事件不改变当前状态。rejected/retired 必须记录原因。

这是因子注册库。包含模型、完整特征实现及输入政策的冻结版本已接入 W6/W7，使用方法见本文后续章节。

## 产物与测试

一次配对运行在 artifacts 下的指定输出根保存：

```text
experiments/<experiment_id>/     # 各种子完整模型/回测及 manifest
runs/<run_id>/
  protocol.json
  candidate_manifest.json
  baseline_report.json
  candidate_report.json
  ablation_report.json
  evaluation.json
  time_block_statistics.json
  paired_report.json
  manifest.json
  status.json
```

专项测试：

```powershell
python -m pytest -q tests/unit/test_factor_registry.py tests/unit/test_paired_selection.py tests/unit/test_time_block_statistics.py tests/integration/test_paired_research.py --tb=short
```

集成测试包括实际 Docker 因子、两个模型种子、费用加倍、独立重训消融、候选执行中断恢复、成功基线缓存复用和引用预测损坏拒绝。全部正式五折、多轮真实 LLM、最终留出和影子日期证据仍需后续验收。

## RDAgent 多轮入口与恢复

- `ResearchSession` 绑定冻结配置、ComparisonProtocol、研发快照、FactorRegistry、FactorEngine 和 GuardedLLM。构造 ResearchContext 时只包含开发折、研发可见字段、组合/模型规则和此前开发反馈。
- `ResearchController` 通过 RDAgent core 的 Scenario、Hypothesis、Experiment、Developer 和 Experiment2Feedback 契约执行；不调用默认股票 Experiment/Workspace。bootstrap 在 settings 单例导入之前注入六个扩展路径，晚注入明确失败。
- 空 ETF Experiment 的 baseline workspace 会实际训练全部冻结种子及压力回测，使用与配对研究一致的共享缓存身份。候选 workspace 只注入本轮 factor.py，运行于固定 digest、无网络、无留出挂载的容器。
- 控制器在提案、编码、运行阶段保存检查点，恢复时重建同一提案版本及 context hash。调用缓存避免重复派发；成功基线及已验证因子复用前核验身份和文件哈希。
- CLI 使用 `research-factor --config <config> --snapshot <snapshot> --run-id <id> --replay <trials.json> --max-trials <n> --stress-min-excess <frozen_threshold>`。replay 是 trial 列表，每项包含 hypothesis 与 factors；每个 factor 为规格字段加 source。格式例与完整调用见 tests/integration/test_rdagent_research.py，测试阈值不自动成为正式政策。
- 同 run_id、相同配置及回放文件重试恢复检查点；配置、协议、源码、依赖或回放内容变化不兼容旧身份。CLI 成功缓存核验研究文件图与外部模型权重，损坏时拒绝复用。
- 退出码：配置错误2、数据未就绪3、执行失败4、质量/完整性失败5、预算暂停6、取消130。因子无增量属于研究结论，保留 rejected/inconclusive，不伪造正收益。

## 金额政策与调用记录

`BudgetLedger` 使用 Decimal 和文件锁，付费调用先预留可信最大金额，再派发。已测费用、未结预留、未知费用次数和总调用次数分别记录；响应写入原子 envelope 后才能提交调用缓存与账本。

- 未明确 budget_mode 或 free_only 时禁止付费调用；确定性 ReplayTransport 明确零费用。
- capped 政策必须有 defensible maximum_cost，未知上界的付费 transport 派发前失败；超额费用如实记录并阻止结果复用。
- unlimited 只表示不设金额上限，运行次数、资源限制、取消与恢复仍生效。
- 本机 RDAgent 0.8.0 文本 API 不返回已核实账单金额。默认 transport 因此将 actual_cost 记录为未知，不记为0，不宣称它能执行付费金额封顶。
- 请求可能已计费而未收到响应时保留 uncertain；没有已验证响应不盲目重发。对账需要账单引用。原子响应已存在而提交中断时，可恢复提交而不二次调用。

## 可信执行与因子契约

CLI baseline 与研究 runner 的固定训练/回测由 NativeBackend 子进程执行，统一时间、内存、CPU、进程树及输出限制。API 请求也通过受限可信子进程；生成的因子代码仅通过 DockerBackend。

研发/留出快照均保存 daily_pv.h5（data key）。单因子保存 result.h5 与 result.parquet，组合特征保持 feature 列组。因子工作器在多个截点执行截断/未来扰动检查，检查标的排列，并为纯时序因子验证单标的与新增标的不污染原结果。宿主收集输出先检查路径位于本轮工作区，随后核验通过的检查清单、数值、索引和覆盖；validated 缓存不挂载给生成代码。

多轮研发以固定20特征起步，后续候选使用已提交的累积特征版本作为基线；候选组消融同时移除新因子及同组历史入选因子。最终冻结前对完整特征集的全部新增组独立重训复核及冻结入口已实现。真实 LLM、正式五折、最终留出及每日影子门禁仍须完成。


## 辅助基线与完整压力账本

baseline 现在同时输出学习模型和两种确定性辅助策略：

- manual_momentum：默认 lookback=20，按冻结交易日历计算 adj_close(t)/adj_close(t-20)-1；缺少任一价格或预热不足时信号缺失，保留目标索引和覆盖率。
- equal_weight_pool：在当时已知合格池中选择全部可买标的等权；保留不可卖持仓。仅将选池数量规则改为全部池，原风险、流动性、费用、交易单位、调仓、组/单标的上限和换手预算继续生效；K 为权重上限时转换为相同 max_weight。
- 模型、动量及等权池使用相同完整 selection 日期和预测诊断样本索引。没有成熟标签不参与 IC 诊断，但完整日组合账本不因标签缺失而缩短。
- benchmarks.momentum_lookback 属于严格配置和 ComparisonProtocol。改变窗口改变协议与运行身份，恢复时不允许沿用旧参数。
- baseline 默认额外执行2倍成本压力；佣金、最低佣金、单边滑点同比缩放，同一组预测保持不变。配对研究仍使用它自己的冻结 cost_multipliers。
- etf_ml.backtest.results.persist_result/evaluate_with_stress 为所有策略保存 report、trades、positions、daily_returns、decisions；压力目录也保存完整持仓和决策，无需仅靠净收益数字解释费用影响。
- baseline_report.json 的 by_fold 继续仅含模型，新增 auxiliary_by_fold 含两种规则的标识、显式执行政策、同样本/日期哈希、覆盖、IC诊断和压力指标。CLI 摘要新增 auxiliary_runs。辅助策略没有学习模型权重，不混入 model_references。

产物位于 <run>/<fold>/auxiliary/<strategy>/，压力产物位于 cost-2.0/。全池基准是一项不同选池规则的辅助对照，主基准始终是沪深300指数。上述实现不代表正式开发折、真实数据或最终投资验收已通过。

新增专项测试位于 tests/unit/test_auxiliary_baselines.py 和 tests/integration/test_qlib_corporate_actions.py。实际 Qlib 企业行为夹具覆盖非调仓日现金分红、1.5倍份额拆分及退出研究池后不可交易持仓保留；价格相应调整，确认分红/拆分日净组合收益不被重复计入。冻结 fit 参数在传入模型前复制，实际 XGBoost 测试验证输出评价字典不改写配置。


## 实际持仓暴露与入选硬门禁

etf_ml.backtest.metrics.position_exposures 从每日 Qlib Position 的真实份额、原价估值和现金计算暴露，核验其与账户权益一致。跟踪组使用估值日或之前最后已知的非缺失分类，不向前读取未来分类；找不到历史分类的持仓统一计入未分类组，单独报告未知暴露，不把它伪装成已知组。

每次基础和压力回测保存 exposures.parquet，包含每日最大单标的权重、最大跟踪组权重、现金比例、未知组比例、持仓数及组数。指标摘要包含 max_single_weight、max_group_weight、max_unclassified_weight、mean_cash_weight、max_holding_count 和映射规则。零份额不计为持仓；负份额、无效估值、无效现金或权益无法核对时报质量失败。

固定入选规则检查候选及每个压力情景的实际集中度。单标的上限取 max_weight；K 为权重上限时取二者较小值。跟踪组上限取 max_group_weight。存在未知分类持仓则不能自动入选；缺少有限暴露指标、权重范围错误或组最大暴露小于单标的/未分类最大暴露，评价直接 failed。实际超限为 rejected，即使它来自非调仓日价格漂移或不能卖出的既有持仓，也不会通过调高阈值掩盖。

人工数值与未来分类测试使用真实 Qlib Position，见 tests/unit/test_position_exposures.py。基础及压力超限、未知分类和缺失/不可能指标测试见 tests/unit/test_paired_selection.py。上述门禁证明工程按冻结政策检查，正式政策数值及真实数据仍须 G0 验收。

## 执行结果与费用归因

etf_ml.backtest.execution.execution_metrics 校验逐笔执行账本并输出每日 execution.parquet。日期与组合日收益相同，非交易日内的无订单记录为零；纯现金期间保留全部估值日期。基础、成本压力、学习模型和辅助策略均保存此报告。

逐笔 trades.parquet 增加 reference_price、slippage_cost、status、reason、historical_average_amount。status 根据实际份额区分 filled / partial / unfilled，仍保留原 commission、amount 及执行价。reason 来自独立账本或无可用参考报价，部分成交的流动性/现金/取整限制当前为聚合原因，并非逐约束归因。

摘要报告 trade_count、filled_order_count、partial_order_count、unfilled_order_count、unpriced_order_count；unfilled_rate 保持仅完全零成交的订单比例，incomplete_order_rate 同时计入部分成交。统计对象是实际交给 Exchange 的订单，不包含组合约束阻止生成的意图；此类跳过原因继续见 decisions.json。

reference_notional_fill_rate 使用有可用原始参考价的请求及成交份额乘参考价计算，不将缺价请求按零金额伪装成全部成交。没有已知价格请求时该指标为 null，并明确无价订单数；不同ETF的份额不直接混合作为金额成交率。

费用汇总包括 buy_notional、sell_notional、executed_notional、commission、slippage_cost、total_execution_cost。滑点成本为成交份额乘执行价与原价的绝对差，买入必须不低于原价、卖出必须不高于原价。execution_cost_over_initial_equity 与 execution_cost_over_traded_notional 分别按初始资金和成交额计算；无成交额时后一比例为 null。total_execution_cost 是归因指标，滑点已计入执行价，不能二次扣减现金或净收益。

账本拒绝非有限/负数量与成本、超请求成交、金额/滑点不匹配、无有效价的成交、成交状态错误、评价区间外日期和零成交收取费用。人工四订单夹具验证完全/部分/零成交、缺价和费用数值；实际 Qlib 夹具验证部分成交及纯现金；三模型和辅助策略验证各情景报告落盘与逐笔汇总一致。见 tests/unit/test_execution_metrics.py、tests/integration/test_qlib_backtest.py、tests/integration/test_baseline_pipeline.py。

上述工程证据不代表真实数据 G0 或完整投资验收通过。

## 奇零份额、整手边界与同日流动性

实际 Qlib Exchange 原有链路会在流动性裁剪阶段提前取整，导致50份、150份及非整数份额清仓失败；本项目 ETFExchange 现在先裁剪流动性，卖出再与实际持仓比较。覆盖全部实际份额时保留精确清仓数量，部分卖出仍向下整手取整。请求超过持仓时不卖空，逐笔报告保留原请求及实际成交数，状态为 partial。此规则需要在真实 G0 按标的交易规则确认，合成夹具不构成交易所规则来源证据。

Qlib 默认 round_amount_by_trade_unit 加0.1份容差，在现金或流动性略低于一手时可能向上成交。本项目原价/原始份额执行使用严格向下取整；人工边界验证不足100份额度时不会成交100份。可买金额同时满足佣金比例和最低费用两项不等式，比例佣金为零时不依赖 Qlib 的费用比例除法。极小奇零份额卖出不足以支付最低佣金时保留持仓，已有现金足以补足时可正常清仓。

Exchange 按 (日期, instrument) 累计实际成交额和份额。买卖两个方向共享历史均额乘参与率的日额度，不用上一方向份额乘当前滑点价格估算已用额度；次日额度自然独立。调用者重建 dealt_order_amount 字典不能清空本 Exchange 的币种账本；若声称存在本 Exchange 未记录的先前成交，则在更新现金/份额前报错，避免猜测其成交价。

独立 Account 继续采用另一条份额/费用计算路径，逐笔验证 Qlib 更新后的现金和持仓。部分成交也记录 liquidity_cash_or_lot_limit 聚合原因，完整逐约束归因仍待扩展。

tests/integration/test_qlib_execution_boundaries.py 使用实际 Qlib Position 和 Exchange，覆盖全部奇零/非整数份额、超持仓请求、部分卖出、同日剩余额度、次日清仓、最低费用、零比例佣金、方向性买卖限制、缺报价后恢复、双向不同滑点价成交额共享、调用方预算重置及未知先前成交拒绝。企业行为端到端夹具另外验证1.0033倍拆分后的月中实际轮动清仓，分拆日收益不被重复计入。

正式真实数据 G0、跨企业行为缺报价时的估值证据及独立投资验收仍未通过。

## 跨轮累积特征与候选组消融

FactorSpec.research_group 在提案阶段声明，属于因子不可变版本；默认 unclassified 将未分类候选放在同一组。基线20个固定特征仍保留为原始对照，组消融针对研发新增因子组。改变组定义需新版本，不能在看过消融结果后改组。

FeatureSetStore 发布 accumulated_research 类型的不可变组合，保存完整 features.parquet、基线定义、父版本、候选规格/源码、分组、验证结果哈希、已提交接受证据哈希与选择协议。版本身份绑定完整组合及结果哈希，文件原子发布，已有版本重用前核验。该状态不是最终 frozen 部署包，因子保持 candidate，不能以工程发布替代G3。

ResearchSession 从初始协议派生活动协议，仅更新 baseline_feature_set_id；标签、折、模型、种子、交易/成本/风险规则继续相同。select_baseline 核验接受证据、父版本、完整研发索引，并从快照重算原始20特征逐值核对。费用/模型/代码环境规则不同的旧组合不能静默导入，退休因子不能继续作为有效累积输入。每次新增仍核验完整候选缓存 manifest 与原始输出，避免只保留文件哈希却修改组或上下文。

run_paired 支持经过 FeatureSetStore 验证的 baseline_override。每轮包括：
- baseline：当前累积特征。
- candidate：累积特征加本轮单因子。
- ablation：独立重训移除本轮单因子。
- group_ablation：独立重训移除本轮因子及所有同 research_group 的历史入选因子；若仅有本轮因子则复用已经独立重训的 ablation，不重复训练。

所有情景保持相同目标样本、模型与成本压力规则。group_ablation.json 记录移除列、组、特征版本及逐折/种子增量。额外组门禁要求逐折种子中位增量的中位数为正、多数折为正、各种子跨折中位增量为正；不一致/缺失证据 failed，无增量 rejected。它不能推翻原收益、风险、集中度和费用硬门禁。开发期反馈只展示白名单组状态与数值增量，不暴露路径或最终留出。

同一 trial 的多个候选先共享同一基线完成检验。仅晋升一个通过全部门禁的候选：按逐折种子中位净超额增量的中位数降序，再按 factor_id 升序处理并列。其他候选保留库中证据，不能把各自通过的因子未经联合检验直接合并。下一轮的 existing_features、candidate_groups、protocol_id 与缓存身份随已提交基线更新。

Controller 的 checkpoint 保存活动特征/协议；trial 记录晋升结果和下一基线。恢复时检查它们与不可变 trial 链一致，并加载验证后的组合。组合已发布但 trial 尚未提交时中断，恢复旧基线、重用调用及实验缓存、幂等重用组合，不二次添加；session.json 同时支持活动组合保存/加载。

验收证据：
- tests/unit/test_feature_sets.py：接受证据、两次累积、组移除、固定晋升排序、父版本/初始值重写拒绝、文件损坏、退休因子和政策变化。
- tests/integration/test_accumulated_research.py：实际Docker因子及受限子进程Qlib多种子训练；baseline/candidate/ablation/group_ablation 分别使用21/22/21/20特征，保存/加载与缓存复用。首个接受记录是明确的合成治理夹具，不能证明其投资有效性。
- tests/integration/test_accumulated_controller.py：受控验证/财务结果下，两轮实际RDAgent控制链及零费用回放；发布后、提交前中断，恢复后22特征且只派发6次调用。该夹具验证恢复治理，实际Docker/训练由前一测试独立验证。

完整特征集全部新增组复核、研究特征冻结及多模型比较入口已实现。显式完整模型冻结及版本注册已实现；独立最终验收工程入口已接入；仍需正式五折、真实LLM、真实独立最终验收与每日影子证据。

## 最终研究特征冻结与多模型比较

freeze-features 读取研究 session.json 的初始协议和已提交活动特征版本。它使用冻结 LightGBM、全部开发折、全部种子及协议 cost_multipliers，重新训练完整特征集和分别移除每个新增 research_group 的特征集。全部组贡献须确认，完整集必须满足风险、实际集中度、未知分类及费用压力收益门禁。缺失或不一致证据报质量失败；无组增量或超限保留拒绝报告，不发布冻结包。没有新增因子时可复核原始20特征基线，不伪造因子优势。

在查看最终留出结果之前，必须配置 acceptance 的全部字段：minimum_net_return、minimum_excess_return、maximum_drawdown、maximum_annualized_volatility、maximum_execution_cost_over_initial_equity、minimum_effective_dates。默认均为 null，未明确则冻结失败。这里记录的阈值供后续独立投资验收使用，研究特征冻结不证明这些阈值已在留出满足。完整模型矩阵必须恰好包含 Ridge、LightGBM、XGBoost，LightGBM 不得改变原研究配置，种子至少两个且不重复。

```powershell
python -m etf_ml.cli freeze-features --config configs/project.yaml --session <research-root>/session.json --run-id <freeze-run-id>
python -m etf_ml.cli compare-models --config configs/project.yaml --snapshot <snapshot-path> --frozen-features <frozen-feature-path> --run-id <comparison-run-id>
```

上述为参数格式。当前 project.yaml 的数据证据和歧义参数尚未补齐，不能直接用它通过真实 G0 或正式冻结。研究控制器的 session.json 路径以该研究根目录的已保存会话为准。

冻结包位于 artifacts/frozen_features/<freeze-id-prefix>/，包含 features.parquet、完整特征定义与源代码、所有会话 JSON 试验历史（包括拒绝）、快照/协议/模型/验收阈值/环境/源码身份，以及独立重训报告的引用。所有包文件绑定哈希。因子从 candidate 转为 frozen 完成后才写 published.json；部分因子转态后进程中断可复用原复核和冻结数据继续完成，不重训、不重复添加因子。

加载核验发布标记、包文件、完整快照、代码/环境、原始因子接受证据及当前 frozen 状态；重算原始20特征并逐值核对，核对完整研发索引和全部组消融证据。修改模型配置、投资阈值、时间角色、特征顺序，或损坏外部试验账本/模型权重，会阻止使用或缓存复用。因子退休后旧包不可继续作为有效版本。

compare-models 在该冻结特征上执行全部模型×种子×开发折，所有种子都保留，费用压力沿用研究协议而非强制2倍。检查冻结折边界、角色样本和日收益日期一致，模型参数准确、账本已核对、指标有限且费用情景完整。comparison.json 报告各模型的跨折/种子收益中位数、超额中位数、最差回撤及完整逐次结果；排名为开发阶段诊断，selection_status=awaiting_explicit_model_freeze，不自动提升赢家、不读取或评价留出。

专项证据：tests/unit/test_finalize.py 验证两个组的独立复核、拒绝发布、风险门禁、部分冻结中断恢复、阈值前置和无效模型矩阵。治理财务结果为明确合成夹具。tests/integration/test_finalize_cli.py 执行实际 Qlib 三模型、两个种子、3倍费用压力，验证原20特征冻结、同日期比较、模型列顺序、幂等缓存、阈值变更和外部账本/冻结数据损坏拒绝。它没有运行最终留出，不构成真实投资验收。
## 显式完整模型版本冻结

compare-models 的 CLI 摘要增加 comparison_path，指向该比较自己拥有的 artifacts/comparisons/runs/<run-id>/comparison.json。freeze-model 只接受已完成且文件/配置/快照/特征/模型矩阵证据一致的比较报告，要求明确指定模型、开发折、种子和非空选择理由；不会按排名自动挑选赢家，不重新训练或扩张调参，也不读取留出。

```powershell
python -m etf_ml.cli freeze-model --config configs/project.yaml --frozen-features <feature-freeze-path> --comparison <comparison-path> --model ridge --fold <declared-fold> --seed <declared-seed> --selection-reason "<development-selection-reason>" --run-id <model-freeze-run-id>
```

这里冻结的是显式选择的开发模型权重，其训练/早停/选择窗口完整保留；不暗示已经自动用全部开发数据重训。用户可选择已声明矩阵中的任一模型版本，但该版本全部开发角色必须早于最终留出起点，权重、训练样本、处理器拟合索引、列顺序、标签跨度和训练池须与比较及特征冻结协议完全一致。

模型包位于 artifacts/frozen_models/<version-id>/，包含 model/<model-id>/bundle.pkl 和 manifest.json，以及复制的完整 features/ 冻结包（含因子源代码与试验历史）。顶层 manifest 绑定明确选择、模型/特征/快照/协议、全部验收阈值、配置、环境、源码版本和比较证据哈希。文件哈希参与不可变版本ID，模型注册成功后原子写 published.json；重试不覆盖同版本或静默改变选择。同 run_id 改选模型会报配置失败。

ModelVersionRegistry 位于 artifacts/model_versions/，定义和决策事件均不可变，head 原子提交；开始状态 frozen，尚未投资验收。库支持 accepted/rejected 的独立留出证据绑定与 retired（必须给出原因）状态，完整决策证据复制到注册库，后续外部文件变化不改写历史。独立留出执行入口已实现并接入合成行情实际 Qlib 推断/账本；状态链合成夹具及合成实际运行均不能作为真实投资验收。

load_frozen_model 核验发布、文件、环境/代码、冻结配置、版本状态、完整特征证据、比较所有试验/权重以及选中模型和处理器的对应关系。模型文件只从明确可信包目录反序列化；Qlib 首次导入可选模型的诊断保留在 stderr，CLI stdout 保持单份 JSON。缺失/损坏发布记录和权重报质量失败，退休版本默认拒绝。加载不使用“最新实验”或只替换权重。

证据：tests/unit/test_model_versions.py 覆盖注册幂等、历史哈希、独立证据身份/复制、拒绝非法状态/退休理由、错误选择、处理器与留出边界、注册发布前中断恢复。财务决策均为明确合成夹具。tests/integration/test_finalize_cli.py 在实际 Qlib 三模型比较后显式冻结 Ridge，确认复制包预测与原始逐行预测一致、缓存重用、选择修改拒绝、发布标记/复制权重损坏拒绝及退休加载拒绝。没有运行最终留出、实际每日数据或下单。
## 独立留出验收与一次性使用

```powershell
python -m etf_ml.cli evaluate-holdout --config <frozen-config.yaml> --frozen-model <frozen-model-version-path> --run-id <holdout-run-id>
```

入口只读取已冻结模型及其完整特征包。holdout_independent 必须在冻结配置中为 true，数据快照与验证配置的 holdout_start 相同，全部验收阈值已明确。当前 project.yaml 不满足真实准入和独立性确认，不能直接用于正式最终验收。

独立执行受 NativeBackend 的冻结时间/CPU/内存/输出限制。模型只 predict，保持原权重与原处理器，不 fit、不根据留出调参或换模型。使用完整历史窗口重建同一特征，逐值核验所有原研发特征；最新推断不依赖未来 label。基础与冻结倍率的费用压力均保存真实 Qlib report、trades、positions、daily_returns、decisions、exposures、execution。标签仅用于已成熟的独立 IC 诊断，不改变组合评价日期。

冻结新增因子通过 FrozenFactorBackend 运行，只有与冻结包精确一致的 FactorSpec/源代码、可信检查 harness 和既定命令可执行。只挂载已验证快照对应的私有行情表和当前工作区，保持只读根、禁网、无宿主凭据及资源限制；正常研发 DockerBackend 仍只允许研发视图，不新增可切换留出的通用参数。独立因子产物和反馈永不进入 research workspace 或 Agent 反馈。该后端也预留明确的 daily_inference 用途，日常信号入口仍待完成。

结果分别报告绝对收益、沪深300超额、回撤、波动、实际集中度/未知分类、币种成本、有效日期、IC、按月收益及同日期移动时间块不确定性。基础检查净收益/超额阈值，基础与压力都检查冻结风险、集中度、费用及有效日期；压力额外检查原研发协议 stress_min_excess_return。缺失/非有限/不可核对的指标属于质量失败；投资阈值未达属于有效的 failed 投资结论，CLI quality_status=passed、investment_status=failed、退出5。工程执行崩溃不伪造金融指标。少于三个时间块时稳定性 diagnostic=inconclusive，既定有效日期阈值仍如实检查，不宣称长期效果。

artifacts/final_acceptance/usage/ 保存不可变使用 claim 和哈希链接的 started/technical_failed/completed 事件。claim 在实际读取/计算留出前建立，所有重叠区间在项目内共用禁用规则：不同模型版本、快照、配置或协议不能重新评价该区间，包括只改阈值、改费用或换种子；新前瞻区间不得与旧区间重叠。相同冻结身份可技术重试或读取原结论，不重新开始选择。

第一次调用确定独立计算的 canonical run_id，后续同候选新 CLI run_id 仍指向 artifacts/final_acceptance/runs/<canonical-id>/ 的原计算。同协议技术失败保留完整 attempt、参数身份、代码版本、错误类别及审计链。计算已完成但模型决策/usage 发布被中断时，独立 RunStore 显式保留已完成计算；恢复只核验原报告并完成提交，不重算该区间。普通运行存储的失败语义保持原样。缓存复用核验权重/特征/全部账本、日期身份、冻结阈值及 usage 审计，不寻找“最新实验”。

最终通过/未通过结论作为精确版本的独立证据复制到 ModelVersionRegistry，状态转为 accepted/rejected；不自动启用或发送订单。已拒绝版本仅允许读取同一独立结论；实验影子运行仍需按日常入口规则明确标记，退休版本继续拒绝。

证据：tests/unit/test_holdout.py 验证绝对/超额/压力/风险/成本/有效日期阈值、指标关系、独立性前置、结果身份及模型不训练声明、项目级重叠拒绝、审计损坏及技术重试；tests/unit/test_holdout_commit_store.py 验证完成后提交失败的显式保留模式和普通默认模式。tests/integration/test_holdout_pipeline.py 在合成行情实际 Qlib 三模型比较与显式冻结后，执行留出成功/收益阈值失败，模拟 Native 技术失败和决策提交中断，验证原权重/研发文件未变、40个固定评价日、3倍成本压力、缓存/提交恢复不重算，以及换候选重用和账本损坏拒绝。tests/integration/test_frozen_factor_inference.py 以明确的合成批准元数据运行实际容器因子，验证21列完整推断/历史一致性，拒绝源码、命令和视图改变；其批准元数据校验被单独隔离为夹具，不代表真实因子通过投资验收。
## 每日影子信号、监控与完整版本恢复

`daily-signal` 是手动触发的影子入口。必须提供标准化增量快照、摄取完成凭据、纸面账户和带时区的观察时点；使用显式 `--frozen-model`，或使用手动 `activate-model` 启用的完整冻结包。入口不会训练模型、修改纸面账户输入或发送订单。未完成/未通过投资验收的版本可验证工程链路，输出保留 `investment_status` 与 `experimental=true`。

```powershell
python -m etf_ml.cli activate-model --config <frozen-config.yaml> --frozen-model <complete-package> --selection-reason "显式启用影子版本" --run-id shadow-activate
python -m etf_ml.cli daily-signal --config <frozen-config.yaml> --snapshot <incremental-snapshot> --ingestion <receipt.json> --account <paper-account.json> --as-of "2026-09-14T16:30:00+08:00" --run-id daily-20260914
python -m etf_ml.cli restore-model --config <frozen-config.yaml> --selection-reason "恢复上一个完整影子版本" --run-id shadow-restore
```

配置必须与冻结版本一致。增量快照可以使用新的源目录和外部文件，但量额/价格/涨跌幅定义、交易单位、字段映射、基准身份、留出边界和池政策不变；研发期原价、复权及历史池逐行一致。冻结因子用原始源码在专用受限容器执行，基线与新增因子的研发历史值必须重现；不允许用空列代替已入选因子。

摄取凭据是可信数据生产方的完成声明，不由本入口虚构。例如：

```json
{
  "schema_version": 1,
  "snapshot_id": "<64位快照ID>",
  "snapshot_manifest_hash": "<快照manifest的SHA256>",
  "trading_day": "2026-09-14",
  "completed_at": "2026-09-14T16:00:00+08:00",
  "complete": true,
  "expected_instruments": ["510300.SH", "510500.SH"],
  "missing_instruments": []
}
```

`expected_instruments` 必须覆盖观察时点已知、正常运营且属于冻结资产范围的完整池，不能只填成功读取的标的。对应当日记录和收盘报价必须齐全。交易日、快照截止日期、摄取日期和纸面账户日期一致；摄取完成不得晚于观察时点或早于收盘。观察时点统一转为上海时区；缺少显式时区按配置错误处理。行情未收齐、非交易日、账户历史未更新或未来执行日历覆盖不足时保存 `skipped` 与原因，退出码3，不发布当天成功信号。

纸面账户显式提供真实份额和现金，示例为调用方确认的空仓账户：

```json
{
  "schema_version": 1,
  "trading_day": "2026-09-14",
  "cash": 500000,
  "shares": {},
  "equity_history": [{"date": "2026-09-11", "equity": 500000}]
}
```

份额和现金应已处理截至当日的已知企业行为。权益历史为连续交易日序列，至少更新到上一交易日；若包含当日，必须与当日现金/份额估值一致。缺报价的已有持仓继续保留，使用已知历史估值并调整此后已知分红/拆分，输出估值日期；不能因此视为可交易。最大回撤根据纸面权益峰值计算；年化波动率使用最近20个日收益和冻结年化交易日数，不足20个收益时保留不可卖持仓并暂停新增风险敞口。

信号日和执行日分别保存：信号在当日收盘后计算，执行日为下一真实交易日。只有下一交易日属于冻结的月中/月末节点才生成目标和意图；非节点继续推断及监控，保留当前仓位。意图是当日原始收盘价的估算，逐笔使用冻结滑点、佣金、最低费用、整手、卖出持仓和历史成交额参与上限，并标明下一开盘价格与限制尚未知。`filled_shares` 等字段属于影子估算，不表示真实成交；账户输入不被更新。

产物保存在 `artifacts/operations/signals/YYYY-MM-DD/`：`scores.parquet`、完整冻结特征重算文件与因子证据、`paper_equity.parquet`、`order_intents.json`、`monitoring.json`、`daily_report.json`、配置/依赖/请求/运行日志与文件哈希。监控包括输入截止及摄取延迟、逐列覆盖率、研发期中位数/IQR分布偏移、与上次成功信号的共同标的排序相关、当前及目标集中度、风险与生成耗时；漂移只用于诊断，不自动改写模型或推定收益。

同日相同快照、模型和账户可用不同请求名重复读取同一成功产物；成功后改变模型/账户/快照不能覆盖当日信号。行情跳过不占用成功日；计算失败保留尝试，更新输入后保存原失败目录再重试。每日计算已完成而请求提交中断时保留计算完成状态，重试不重复推断。历史监控引用逐层验证各成功信号的日期顺序、完成状态和全部文件哈希。

`activate-model` 和 `restore-model` 都必须有显式理由。版本事件不可变且逐条链接；发布中断使用已保存意图恢复，同一请求不重复发布。恢复加载上一个完整冻结包，包括权重、原预处理器、全部特征定义及原协议，不只替换权重。包的依赖/代码版本必须兼容，原快照、注册库与比较引用证据应保留；退休或损坏版本不能恢复。这些操作只选择影子版本，不改写独立投资验收状态。

当前测试分为输入/风险/估算/幂等单元夹具、合成治理版本恢复夹具，以及实际 Qlib 冻结模型对合成增量行情的推断集成。真实 G4 仍要求至少10个真实交易日并覆盖两个冻结调仓节点；上述合成测试不替代该门禁。

## 企业行为校验与每日独立账本

`etf_ml.data.actions.validate_events` 在快照发布前及实际Qlib初始化前检查企业行为。非空事件表必须包含唯一非空 `event_id`、本地交易日期 `datetime`、快照内实际代码 `instrument`、有限非负 `cash_per_share` 与有限正 `share_multiplier`。无经济效果的记录不接受。默认 `sequence=0`；同一天同一ETF有多条记录时必须显式给出唯一非负整数顺序，不以文件行序或事件名推测经济顺序。有登记日的分红按登记日原始份额计量；未声明登记日的兼容事件按该事件执行前份额计量。

可选 `available_time` 一旦提供必须带时区，且不晚于生效日09:30（上海时间）；保存为统一上海时区，重复校验稳定。现金模型支持同日到账及延期应收：提供 `pay_date` 时，现金事件要求它不早于 `datetime`，按日期区分应收和可用现金；提供 `record_date` 时要求它为已覆盖且严格早于除息日的交易日，资格按该日收盘持仓记录。不符合资格时明确拒绝，不能把除息日当成实际到账日。上游基金分红接口明确区分公告、登记、除息及派息字段。[官方字段定义](https://tushare.pro/document/2?doc_id=120)

事件表结构通过不等于来源完整。快照 `data_quality.json.events_validation` 明示 `source_completeness_verified=false`，记录事件数、公告时间未知数与日期应收模型。空事件表也不能证明真实区间无企业行为。来源按用户确认的Tushare接受，正式G0仅需检查事件覆盖和原价/复权关系，异常才追溯，不逐条跨源复验。延期派息应收及非相邻登记日权益记录已实现。

`raw_close_as_of` 只读取估值日及之前的有限正原始收盘价。没有当日新报价时，从最后报价之后按事件日期和显式顺序调整估值：`(旧价 - 每份现金) / 份额倍率`。现金记入账本、份额改变与旧价调整共同保持企业行为前后权益；恢复报价时直接使用真实原价，不再次减现金或除拆分倍率。即使退休或不可交易，有新报价的持仓仍须按该报价估值；没有新报价时保留事件调整后的持仓，不删除份额，也不制造可交易行情。独立Account和Qlib策略已同时修正分红缺价时的旧估值；无效调整在现金/份额变更前拒绝，重复事件ID改变内容也会失败。

所有实际回测的基础与费用压力目录现在保存 `ledger.parquet`，与 `daily_returns.parquet` 逐日同索引。独立重放先处理当日事件，再逐笔核验成交数量、原价滑点、成交额与冻结佣金，随后计算现金、份额、原价估值与净收益；不使用Qlib报告收益推进独立权益。每日分别核对Qlib现金、所有持仓份额、总权益及净收益，任一不一致就失败。

日账本列为 `independent_cash`、`independent_equity`、`reported_equity`、`independent_return`、`cash_residual`、`maximum_share_residual`、`equity_residual`、`return_residual`、`held_instruments` 与 `stale_valuation_instruments`。指标记录 `daily_accounting_reconciled`、日期数、最大现金/权益/收益残差及缺新报价估值日期数。原 `accounting_reconciled=true` 现在只有完整日账本核对通过后才发布；该值不能仅由逐笔成交成功推导。

研发基线读取的事件显式裁剪到研发视图，不把留出事件交给模型研究回测。每日增量输入除原行情及PIT池外，还核对最后研发日期及之前的全部企业行为，与冻结快照逐项完全一致；改写历史分红或拆分时拒绝生成/复用信号。

专项证据为26项事件校验测试、12项手工日账本测试及实际Qlib缺报价企业行为集成。人工账本明确核对50万初始资金、单边0.03%滑点和千分之3佣金，故意修改现金、份额、费用、成交额、滑点、日收益或日期会失败。实际集成覆盖连续14个交易日无报价期间的分红与拆分、退出研究池后保留不可卖持仓，以及恢复报价后的真实估值；收益和金额分别核对。现有三模型/辅助策略基础与压力、最终独立留出和每日冻结历史集成继续使用合成行情验证工程行为，不构成真实G0、G3或G4投资证明。

## 开发期诊断与紧凑 Agent 反馈

配对实验复用已保存的各折/种子预测和组合指标，新增 `development_diagnostics.json`；不额外训练模型，不访问留出视图，不改变入选门禁。诊断中的选择期标签按同一 `learning_mask` 和资格池过滤，核对 `evaluation_index_hash` 与模型的实际评价样本一致。

- 质量：已验证的覆盖、最差日期、非有限数/重复键、因果检查和索引检查；另报告选择期预热后的资格池各折/跟踪组覆盖。
- 预测与组合：各折/种子的基线和候选 IC、RankIC、有效日期，以及净收益、超额、回撤、波动率、换手、佣金、滑点、执行成本和集中度；指标直接复用真实实验报告。
- 因子观察：选择期单因子逐日 IC/RankIC、前后半段稳定性、与已有特征的日截面绝对 Rank 相关中位数。固定 1、h、2h 预测跨度在共同成熟样本上报告 IC 衰减，不按结果挑选跨度或修改正式标签。
- 稳健性：逐折/种子的费用压力、独立候选消融、候选组贡献（既有 `group_ablation` / `group_paired_deltas`）和时间块不确定性。跨种子结果逐项保留，不将种子数作为独立日期数。

`ETFFeedback.structured.by_candidate[].observations` 只传摘要，不传 `by_date`、文件路径、模型内部对象或任意上游字典。相关性只传前三个既有特征，分类覆盖只传三个最差组及组数；完整明细仍在本地。白名单同时检查指标为有限数，无法计算的 IC 和未提供的诊断保留 null/`unavailable`，不填0。诊断必须匹配开发阶段、协议、基线和候选，否则拒绝反馈；IC 再高也不能推翻已拒绝的入选决策。

反馈保留方向、适用范围、版本/运行身份、原因代码和技术失败阶段。`factor_validation` 区分候选生成/验证失败，`model_evaluation` 标记配对训练/回测失败；无增量属于开发选择结果。技术测试使用合成行情，仅证明观察与隔离行为，不构成投资验收。

混合企业行为表可以在纯拆分/合并行将 `pay_date`、`record_date` 留为 NaT；现金分红行若声明这两列则必须有合法日期。非现金行已填写的日期仍需为本地正常化日期。现金登记日按实际收盘权利记录核验；延期付款另按下节应收账本处理。

## 延期分红应收与可用现金

现金事件 `datetime` 表示除息日；`pay_date` 若省略则采用同日到账兼容模式，若提供则不得早于除息日，可以超出当前快照截止日。除息日按前一交易日收盘持仓计提每份现金，旧估值扣除每份分红，同时按独立事件顺序调整份额。付款日在后时，款项计入应收和总权益，不增加可用现金；在首个不早于付款日的模拟交易日转为现金。期间卖出或拆分持仓不改变已取得的应收金额，到账不是新的收益。

独立 `Account` 保存按事件ID归属的应收，付款后清除；再次结算或重放同一事件不会重复付款，同ID修改付款日会被拒绝。`ETFPosition` 将应收置于独立属性，计入 `calculate_value()`，不放入股票ID列表或 Qlib 的 `cash_delay`，不受其每步结算释放。独立每日重放同时核对可用现金、应收、份额、权益和净收益；新增 `independent_receivable`、`reported_receivable`、`receivable_residual`，以及最大应收残差指标。

全情景另保存 `receivables.parquet`（日期、事件ID、标的、金额、除息日、付款日）；没有应收时仍保存标准空表。`exposures.parquet` 增加 `receivable_weight`，现金、股票和应收权重之和必须为1，避免把待到账款项标成现金。

纸面账户可以显式提供 `receivables` 列表，每项包含 `event_id/instrument/amount/ex_date/pay_date`。这仍是调用方提供的纸面状态，不证明真实成交；必须已取得且未到付款日，并匹配快照中现金分红事件的ID、标的、除息日和付款日。估值和风险包括应收，买入估算只使用可用现金。影子风险输出分别记录可用现金、应收金额及权重。

登记日权利按真实收盘份额记录：登记后卖出仍保留已取得权利，登记后买入不补发；中间拆分按倍率换算每份除息估值，登记日分红金额保持原份额口径。独立重放与Qlib分别记录并核对权利；未知历史不以除息日份额替代。所有情景保存 `entitlements.parquet`。真实复权因子与事件关系仍需必要一致性检查，不由技术夹具替代。


组合分配器通过 `constraints.receivable_weight` 统一扣除应收，`Allocation.receivable_weight` 明示该部分权益；库存股票加应收不得超过总权益。股票、可用现金、应收权重之和为1。不可卖出存量及原有换手上限继续保留，仅分配剩余可投资权益；影子与实际 Qlib 决策采用同一约束。影子 `cash_weight` 为扣除应收后的计划现金权重，新增 `receivable_weight`，成功产物的权重守恒核验包括三者。


真实RDAgent调用的provider配置从项目根 `.env` 显式加载允许字段，环境变量优先；不会从生成因子工作区搜索配置。可信子进程继承允许的模型/后端/端点/密钥，缓存身份包含非密钥配置哈希，日志覆盖子进程专属凭据脱敏。该配置修复不改变付费预算门禁；未明确预算仍不能派发真实调用。
