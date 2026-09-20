# 项目约定

## ETF Qlib 数据路径

- 用户于 2026-09-13 指定 ETF 数据路径，原始消息为 `D:\qlib\_data\etf\_qlib\_data`。
- 按消息中 `\_` 为下划线转义理解，实际路径为 `D:\qlib_data\etf_qlib_data`；本机已确认该目录存在。
- 原始消息按字面解释的多级目录 `D:\qlib\_data\etf\_qlib\_data` 在核对时不存在；若用户明确要求按字面使用，应再次核对。
- 后续 ETF 数据检查、适配层配置和回测使用上述已核实路径。当前 `.env`、`.env.example` 与环境检查脚本的默认路径已经一致。

## G0 研究参数（2026-09-13 用户确认）

- 研究范围：A 股场内现有正常运营的 ETF。第一版按跟踪境内 A 股的股票 ETF 理解，以有效分类和运营状态识别；当前池保存查询时点和成员依据。每日信号使用当日正常运营且满足资格与交易约束的标的。
- 历史回测按当时已知的上市、分类和运营状态重建历史池，不以当前存续名单倒推全部历史。若只有当前存续数据，需记录幸存者偏差，不能宣称完整历史池验收通过。
- 主基准：沪深300指数，默认使用价格指数口径；行情来源、代码映射和有效区间在 W1 核验。研究池等权和手工动量保留为辅助对照，不替代主基准。
- 初始账户资金：人民币 500,000 元。
- 滑点：0.03%，按每笔单边成交的不利价格偏移解释，配置值为 `0.0003`；买入价乘 `1.0003`，卖出价乘 `0.9997`。佣金按用户最新确认0.03%（配置值 `0.0003`）逐笔单边计收，替代此前千分之3；暂按仅比例收费、不另加最低费用（`minimum_commission=0`）。佣金不包含在滑点内，不重复扣除滑点。
- 时间切分：采用扩展训练窗、半年 early_stop 和半年 selection 的五折时序滚动方案，见 `docs/02-data-and-ml-contracts.md` §4.2；不随机切分。
- 最终留出候选：2026 年至冻结快照的有效数据截止日；仅在核实未用于既有策略选择后启用，否则使用新的前瞻区间。交易日边界、预热区间和标签成熟过滤在 G0 固化。
- 追加参数原值：`K=0.05`、流动性 `20%`、风险 `12%`、调仓“月中和月末”、研发“无预算”。用户后续委托K/流动性使用常规设定：K选当前可买候选前5%，向上取整非空至少1只并等权；流动性为执行日前20日均成交额20%参与上限、同日双向共享。风险明确为最大回撤12%。用户最新明确LLM费用不设上限；佣金0.03%，暂不另加最低费用。
- 调仓频率已改为每月月中和月末，替代每 5 个交易日。日期映射暂按当月15日或之前最近交易日、当月最后交易日作为执行日，使用执行日前一交易日收盘后完成的信号；日期口径为规划默认值，G0 写入冻结日历。h=5 暂保留为预测任务，不再假设它等于两次调仓间隔，组合收益来自实际持仓账本。
- 首轮配置显式冻结LLM金额政策unlimited、比例佣金0.0003和最低费用0（规划假设），常规上限及日期映射沿用配置；首轮max_trials=1，取消/超时限制保留。参数记录不代表 G0 数据验收已经通过。

## 真实字段语义证据（2026-09-13 后续只读核对）

- 全量核对原始 bin 池1,769只 ETF 的本地 CSV/bin，全部10项行情字段按 float32 精确匹配，覆盖1,335,742行有效报价；原始数据、CSV及导出器哈希前后一致。
- 现有导出器使用 fund_daily 原始行情，volume 原样复制 vol，单位为手；amount 单位为千元；change 为价格涨跌额，pct_chg 为百分数涨跌幅。内部成交量×100、金额×1000、pct_chg÷100；不能将 change 作为收益率。
- 原价来源口径由未转换的导出路径支持；factor bin仍缺，但1769只真实factor旁路已采齐并覆盖全部报价；完整企业行为关系尚未验收。原日历含309个周末日期。
- 证据见 docs/07-data-source-evidence.md 和 artifacts/data_evidence/127efea309e9a9741e57f8893d33632c9f8a8b2947db8f34d1ebfd15dbcd4199/export_semantics.json；可选映射为 configs/data/tushare_export_evidence.yaml，不更改默认配置，也不代表 G0 通过。

## 交易日历证据（2026-09-13）

- 沪深两市2020–2026年度休市公告及2020春节临时延期公告独立核对一致；已归档公告、生成脚本和文件哈希，组合证据ID b2e86cde671f974ee89f4f7e9f982d86e50d9c430fae4ad1c767a8c9b325ea4c。
- 新日历共1,697日，实际数据覆盖区间1,619日；全量bin报价日期同为1,619日，无休市日有效报价；原日历340个休市日期，原始文件哈希再次核对不变。
- 可选配置 configs/data/tushare_calendar_evidence.yaml；默认配置不改。2020临时延期发布晚于事后月末节点，日期证据不能倒推公告前调仓决策；PIT日历政策与复权/企业行为、历史池、指数及参数语义仍须正式G0冻结。

## G0 核对与沟通约定（2026-09-13 用户收敛）

- 数据来源已确认为 Tushare。记录接口、下载区间/参数和导出映射；不逐条跨供应商或交易所复验行情，也不重复运行已通过的来源核对。
- G0 只检查影响研究正确性的字段单位、日期映射、数据覆盖、复权/分红处理及时间隔离；异常才进一步追溯。已有量额与日历证据直接复用。
- 耗时的完整来源追溯可后补，不阻止工程开发和合成/技术测试；实际缺失的复权、历史成员和基准数据不能用来源名称代替或虚构。
- 用户希望节省 token。沟通只报告结论、关键问题和下一步；复用已有实验结果，Agent 反馈只传紧凑摘要，完整逐日明细保留在本地产物。


## 当前开发与恢复状态（2026-09-13，替代旧进度记录）

- 完整目标：实现01–04全部功能及测试，列出缺口和优先级，进入第一轮真实RDAgent+Qlib；尚未完成，不缩减为技术回放。当前开发/测试计划见docs/08-first-loop-readiness.md，历史实现证据见05。
- 全部单元+整个集成目录667项通过，0失败/错误/跳过，592.09秒、757库警告；artifacts/tests/full_artifact_regression.xml，session21824已exit0。之后仅首轮预检补齐声明限价/分红审查/份额旁路文件缺失拒绝，相关33项通过3.11秒（first_loop_final_preflight.xml）；范围重叠，不伪称670全量回归。
- 已实现标准化/快照、PIT池、因果特征与成熟标签、三模型、Qlib/独立每日账本及应收/份额取整、Docker门禁、RDAgent配对/多轮恢复、因子/模型注册、完整冻结/比较、独立留出治理、每日影子与恢复。真实LLM、正式真实G0–G4、投资和真实10交易日影子未完成。
- source_hashes使用filesystem_path枚举与文件判断，修复Windows超260字符遗漏证据；相对身份和64位哈希不变。限价汇总全目标门禁、原响应/请求manifest/政策复制闭包已实现，不合格时仅pending诊断，不发布部分池。
- Tushare双接口采集已终态1769只/3538响应，最后2991 exit0，source_collection_job.json=collected；不要轮询旧91868/56856。1344565行factor覆盖1335742有效报价。全池现金映射1766可用/3货币ETF缺日期，未发布合格事件输入。诊断full_dividend_diagnostic_after_review.json；完整factor只在失败汇总诊断temp中，不用它冒充合格快照。
- review.json有4项绑定来源审查：510310登记日、159919除息日、512390现金0.1818、512700除息2020-03-25；512700依据受信Tushare原始参考价/factor，非发行人公告。510310显式份额合并0.49977589、2024-09-23生效、ceil取整，SSE事前公告。四只完整关系通过，four_etf_review_consistency.json；原始数据不改。
- 限价采集已终态：session30649/PID13000 exit0，1648新请求/复用121/1769完整/无失败；limit_collection_job.json=collected，不再poll或重启旧68455/30649。汇总57501/PID19468已终止，质量未通过（逻辑码5）：159657在2023-03-13至17五个真实报价缺限价。limit_assembly_job.json记录原诊断根，未发布qualified输入。针对etf_limit与legacy stk_limit两次窄窗恢复各0行，missing_limit_recovery_report.json绑定原响应/参数/哈希，全池缓存不改。当前无采集/训练/测试进程运行。
- materialize_etf_limits.py全部缓存齐备后才检查全报价覆盖/开盘前时点/原价限价边界，复制原响应、请求和policy并绑定全部哈希。config limits_path尚未指向局部样本。历史09:25可用只是source_documented_schedule工程代理政策，actual_historical_receipts_proven=false；实时要recorded_receipt。
- 历史metadata全部asof2026-09-05，原池缺24只退市ETF，另33待上市；历史区间由行情成员起止生成，不是历史分类/运营证明。historical_metadata_gap_report.json已绑定元数据/生产器哈希；已询问真实历史资料目录，未答，不倒填当前状态。
- 用户已授权LLM费用无上限并确认佣金0.03%、Tushare Pro可用，无需再追问预算/API可用性。最低费用0为暂定比例收费假设，非券商事实；历史资料由开发继续获取。真实RDAgentTransport未知实际账单金额，支持已授权unlimited；尚无付费LLM调用。
- configs/data/tushare_first_loop.yaml复用单位/可信日历/CSI300/4审查/合并路径及3种子固定镜像；metadata/events和PIT尚未完整，佣金及unlimited金额政策已写入，factor/limits不接失败诊断或单只样本。下一步真实五日限价补缺或有证据的有效规则推导、历史范围/企业行为→真实配置→基线与单候选闭环。
- P2冻结包可移植性仍未实现：引用原comparison/snapshot/registry绝对路径；迁移不能靠复制权重、重写immutable版本ID或重置留出禁用账本。


最新实质进展：一次完整1769目标企业行为关系诊断，1605通过/146失败/15不完整/3缺日期阻止；full_action_diagnostic/report.json，recipe diagnose_full_actions.py，session73366/PID21276已exit0，action_diagnostic_job.json=completed。此前“只剩3缺日期”仅指现金映射，不代表其他关系已通过。异常主因140只未知转换（146事件日）、8只现金关系（9日）；没有发布合格输入。

已证实参考价千分位精度误报：DataSpec.reference_close_tick可选（默认None，最大0.001），首轮显式0.001。仅已声明事件日且源参考价在价格点、与精确事件除息价≤半tick+float32误差、因子/源参考倍率仍≤固定0.0005容差时接受；不改精确现金、不推断未知份额。审计/快照/汇总统一，报告原/有效残差和精度事件数。26门禁专项含真实bin审计/快照准入、未知转换/错现金/错因子/非法政策拒绝通过；相关最终全部591单元+55实际Qlib共646项通过70.06秒（reference_precision_regression.xml，519警告），session66358已exit0；667全目录结果在这次修改之前。没有模型或测试进程运行。

仅复查8只现金异常，6只通过、8个事件精度支持；cash_precision_recheck/report.json/recipe recheck_cash_reference_precision.py，session56606已exit0，0调用/源哈希不变，原全池报告保留，不再次全量审计。仍异常159220缺2025-11-10转换；159922缺2022-02-18及2024-12-02同时现金/份额关系（不是精度问题）。结合原诊断剩140关系失败、15覆盖不完整、3映射阻止，非正式全池门禁通过。


环境交付诊断已完成04明确待办：scripts/check_etf_environment.py支持--config/--json分离环境可用性与G0–G4，环境/smoke不升级门禁；G0仅blocked_preflight/needs_full_validation，G1–G4未执行则not_assessed。适配器检查EXTENSIONS模块存在，不初始化RDAgent settings，不因默认Conda名不同误报缺适配器；真实契约仍由测试证明。Docker固定已验证digest、--pull never/--network none、原数据只读挂载。退出1环境失败、2配置错误、5预检阻塞。

19项诊断/首轮预检相关测试通过0.76秒（environment_delivery.xml，新增8诊断测试）；真实本机+固定Docker检查通过，环境WARN仅既存sparsediffpy0.3要求numpy>=2但项目1.26.4，没有改依赖/全局环境。真实报告environment_delivery_report.json绑定脚本、配置、源码和镜像，原provider哈希前后不变；G0预检blocked/实际退出5，G1–G4未评估，无smoke训练或LLM调用。报告采集器session11378已exit0，无活跃进程。此轮只改script/tests/docs，src未改，之前646代码关联回归保持有效。

2026-09-13 最新授权配置已验证：LLM金额unlimited、佣金0.0003、最低费用暂定0、首轮max_trials=1。配置/就绪相关31项通过（artifacts/tests/authorized_parameters.xml）；实际只读预检artifacts/first_loop_authorized_preflight.json中预算与组合参数阻塞已消除，仍缺合格factor旁路、历史metadata和事件输入/PIT证明。factor已采齐但未通过全池事件关系，不代表重新需要采集1769份；0外部调用、0训练。

2026-09-13 份额事件新增：归档华宝159220事前2025-11-04发行人PDF，2025-11-07登记、11-10除权、倍率2、floor整数取整。独立新版本share_announcements_v2保留原510310合并及原证据，首轮配置已引用；explicit_share_recheck/report.json验证159220与510310均通过，原行情哈希不变，非全池G0。与旧全池/精度复查合并观察，关系失败由140降至139；原报告不覆盖。

159922原始发行人公告与列表已归档jsfund_159922/discovery_manifest.json：2022事前公告明确倍率1.117385865及floor；2024事前公告明确倍率2.5及ceil，现金0.1292按拆分后登记份额分配。当前mapper的现金单位默认登记日转换前份额，同日cash/share序号也需显式绑定，尚不能直接追加事件通过；下一步补公告绑定的拆分后现金计量及取整登记权益，并用实际Qlib/独立账本验证，不把0.1292私改成0.323。

最新份额输入相关58项通过6.07秒（explicit_share_input.xml，298第三方警告）；实际只读复查43118和发行人公告归档96121均exit0，无活跃任务。本轮仅配置/产物/文档变更，未修改src代码。下一轮必须实现159922明确的拆分后现金基准、整数取整登记权益及公告绑定的同日顺序，然后真实复查；现有默认record-share算法不可直接声称支持该事件。

2026-09-13 同日拆分分红完成：cash_share_basis默认record，公告支持时post_record_conversions；现金不变，Qlib/独立账本按转换和ceil/floor计算登记权益，cash_basis_overrides精确绑定原分红及证据/顺序。159922为2022倍率1.117385865、floor，2024倍率2.5、ceil、现金0.1292按新份额计量。post_split_cash_recheck/report.json真实159922/159220/510310三只全时序均通过，0新来源/LLM调用，原source哈希不变；仅增量关系复查，结合旧报告剩138关系失败、15不完整、3日期映射阻塞，不宣称全池G0通过。

最新回归全部单元+实际Qlib企业行为/基线共620项通过69.76秒、441警告（post_split_cash_regression.xml）；包含人工101份×2.5→253/252份、精确现金、错误权益拒绝，及原金额/日期/顺序/证据改写拒绝。测试2824和真实复查65899均exit0，无活跃任务。新配置引用share_announcements_v3/share_events.parquet；旧v2/初始事件和原全池报告不覆盖。下一步其余明确份额事件、历史成员和限价补缺→合格快照→真实基线和RDAgent单候选闭环。

2026-09-13 批量实质进展：嘉实四只新增事前拆分/取整公告，7只含既有事件真实复查全通过（jsfund_batch_share_recheck，81401 exit0）；华宝官网分页完整50条，42 PDF已归档、8旧HTML明确pending，20目标事前PDF全部具备且真实复查全通过（fsfund_batch_share_recheck，87177 exit0）。新share_announcements_v5含28份额事件及原159922现金基准注释，首轮配置已引用。精确分配exact只接受十进制整数结果，非整数需实际分配明细；新增实际Qlib/边界测试。

批量采集发现Windows time_ns同值导致原子临时名冲突，已改UUID4，并用固定时钟8线程真实并发验证。早期采集22792/第二次均terminal失败；修复和官方相对附件路径支持后50333 exit0完成42PDF，不能轮询/重启旧句柄。相关86预检通过12.66秒；最新全部612单元+11实际Qlib共623通过66.97秒、479警告（batch_share_regression.xml，53983 exit0）。本轮无活跃进程、无新增LLM调用，原数据不改。

组合进度action_progress/report.json及compose_action_progress.py绑定原报告和全部增量身份：1637关系通过/114失败/15不完整/3日期映射阻塞，33只曾增量重查，1736只未按最新代码再全验，非G0、非qualified全池。docs08已收敛为当前计划，下一步国泰/广发/华夏等剩余明确事件、真实历史成员和五日限价→真实快照/基线/RDAgent。预算已unlimited、佣金最新0.0003、最低暂定0；不重复询问用户已答参数。


2026-09-14 最新增量状态：原数据只读；严格4报价/factor旁路、v25共144份额、原分红/0.0005政策保持。真实关系诊断1751通过/0失败/15不完整/3收益映射阻塞，148增量/1621未按最新代码全池重验，非G0；24空窗口/15上市窗口原证据已齐，9事后公告不倒填。6375实际收益（1967/1967/2441）各覆盖20191230–20260904全2441自然日，两只各341合并区间、511960逐日完整；159001原每百份网页字节/解码/当前UTC回执已绑定，旧来源不重查。IncomeRule及验证移至data.income_supplement，backtest.income兼容导出；IncomeBook未付复利/面值兑付/部分保留/全卖正负结清接Qlib采样前及独立重放，exact拒绝未证分币，技术截尾proxy不得正式快照。新增DataSpec.income_path：逐日parquet含规则声明/规则可用时点、行证据与primary_evidence相对文件/https/hash；拒绝未来披露/越界/未版本化同标的现金份额动作，保存完整及research/holdout收入，研发视图不携带未来原文。共享基线/候选/辅助/压力与留出接入；每日纸面声明已调整收盘份额、当日income_balances/rule_ids/as_of，不重应计，计权益风险但隔离买入现金，历史金额/披露/规则版本及规则时点改写拒绝。income_snapshot_final_regression.xml共702单元+4实际Qlib706通过，0失败错误跳过，319.16秒315第三方警告（71900 exit0）；income_snapshot_frozen_prefix.xml 27通过40.32秒，其中22重叠新增5（14470 exit0）。income_snapshot_closure.json绑定当前代码2e288c39de7502c66b26760b0fabafebbcd39d9904b8c805ba2f4d68a4ad0aa0及报告哈希，旧1记录类型断言已通过，不声称整个integration目录重新通过。此前6375收入账户兼容旧10失败已在72项闭包解决，报告保留。新增收益完整冻结留出/每日服务尚待集成、货币总回报标签尚待另验；真实尾数分配/两只逐日明细/披露时点及完整metadata/events/PIT仍P0，未合格发布真实收益/快照；first_loop_metadata_scope_preflight.json只剩metadata/events/PIT三项，0外部调用/用户数据训练。0真实LLM；冻结费用/参数不重复追问；T42版本14fe最终713项（709单元+4实际Qlib）全通过304.32秒315警告，68754 exit0。新增P0完整quoted历史成员覆盖gate（T43）：未知/未来元数据不隐式缩小全池通过快照，保留已知池外/无报价诊断；定向58通过21.23秒（29776 exit0），最终metadata_quote_scope_regression.xml 715全单元+8实际基线/严格修订真实快照/CLI共723通过，0失败错误跳过，173.89秒319第三方警告（86620 exit0）；metadata_quote_scope_closure.json绑定当前固定代码61bbdab106fc64cb2f1df5ee92d56b87bb071129776eb2db788b0f2abd101a5b和报告哈希，未重跑整个integration。真实历史成员来源仍待补齐，不发布局部G0；所有本轮测试句柄已terminal，无活跃采集。下一步补历史成员真实输入及完整事件资格，保持完整目标范围，不以易通过子集代替首轮。

2026-09-14 用户决定：历史成员若不能快速从 Tushare 获取则先跳过。已核对官方 etf_basic / fund_basic 和已有缺口报告：上市日期、当前状态及基金退市日期可直接复用；完整按历史时点的成员、分类/跟踪变更和公开时间不能由基本信息接口直接取得。暂停耗时历史成员重建，仅此项延期；未授权忽略其他缺失输入。正式 G0 门槛保留，后续当前成员回溯研究须显式记录存续偏差和分类假设。决定见 artifacts/data_evidence/historical_members_defer_decision.json；本次未新增 Tushare 调用或实现运行绕过。
