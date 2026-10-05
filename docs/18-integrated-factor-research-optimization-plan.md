# ETF-Qlib 整体优化规划：从因子接入到可信候选

版本：1.2 ｜日期：2026-09-28 ｜状态：工程实施与持续验收中

本文交付整体路线、工作包、实验设计、验收和运行边界。已实施范围、真实运行结果与未闭合验收项以[实施验收台账](18-implementation-evidence.md)为准；规划要求仍有效，不把设计文档或测试通过等同于研究接受。

后续实施证据与未完成项单独记录在 [实施验收台账](18-implementation-evidence.md)。本规划的工作包和验收要求继续有效；以台账对应的源码、测试和工件核实实际进度。

## 1. 目标与范围

目标是让系统能用有限、可追溯的研究资源，检验成熟公式与 RDAgent 假设，解释失败并筛出可复现的正式研究候选。工程验收以正确计算、可信比较和完整决策证据为准；市场数据中可能没有通过全部门槛的因子，不以“必须找到一个”作为程序停止条件。

本规划承接 [16 正式研究闭环优化](16-formal-factor-research-optimization.md) 与 [17 因子库接入与筛选](17-factor-library-integration-and-screening.md)。16 中已实现的诊断、campaign 台账和记忆能力优先复用；17 中的来源分类继续有效。本文件补充跨模块实施顺序与验收，将组合风险、信号期限、ETF 重复暴露、搜索偏差和费用审计纳入同一规划。

范围：验收状态修正、风险归因、离线因子导入/执行/筛选、ETF 适配、期限诊断、反馈闭环、有限搜索、独立验证准备。策略行为变更在单独版本内实验；模型调参、收费数据采购、其他资产市场和券商交易接口不纳入首批实施。文档完成不触发新 campaign 或留出验证。

## 2. 已确认约束与当前事实

### 2.1 保留的研究约束

| 项目 | 当前确认值 / 处理方式 |
| --- | --- |
| 正式成本压力收益线 | `stress_min_excess_return=-0.03`；评价期策略累计收益减同期基准累计收益，比例值，非年化，也非候选减基线 |
| 压力场景 | 当前协议成本倍数 `2.0`，正式比较仍执行风险、暴露、增量与消融等其余门槛 |
| 绝对回撤验收 | 12%；策略中的风险触发参数与事后回撤验收分别记录，触发不等于保证不越线 |
| 模型与评价矩阵 | 固定 LightGBM 主比较；五个开发折、模型种子 42/43/44；种子不增加独立时间样本量 |
| 标签/调仓 | 当前标签 5 个交易日；月中/月末调仓。变更标签或调仓属于新协议实验 |
| 因子约束 | 最大 lookback 120；覆盖率门槛 95%，严格沿用现有门槛的分母与暖机期定义 |
| Campaign | 用户确认上限 5 次尝试；`formal-five-20260924` 已使用 5/5，不增大既有 campaign 上限 |
| 费用 | 用户已确认 `unlimited`：不设金额硬上限。费用未知仍记录 unknown；不把它解释为零，也不擅自改成金额上限 |
| 运行时间与物理调用 | 与金额上限分离；每trial默认最多5次逻辑dispatch（transport每次最多1次物理dispatch）、最多2次修复；campaign总墙钟默认8小时，写入不可变protocol/campaign策略，修改须新身份 |
| 数据与历史 | 原始数据、冻结快照、旧基线、run 和事件账本保留；新证据使用派生视图或新版本 |

下一次正式 campaign 若按本规划启动，建议仍沿用 5 次尝试并分配新 ID。批量离线预筛的预算独立定义，不能借更换 ID 隐藏此前选择历史。

### 2.2 本次核实的事实与含义

| 编号 | 事实与证据 | 对规划的影响 |
| --- | --- | --- |
| F01 | [最新 R05 报告](../artifacts/runs/tushare-formal-first-loop-20260925-r05/first_loop_report.json)：5 次生成尝试、3 个唯一已评估定义、接受 0，usage audit 为 partial，2 个 open attempts，10 条成本未知记录 | 保留拒绝结果；先核实未结项，不把 open 直接认定为仍在运行或可释放名额 |
| F02 | R05 的 paired baseline 有 15 个折/种子行，12 行最大回撤超过 12%，最差 `0.17106745341281482`，即约17.11% | 查明基线继承风险；不能把所有失败归因于新增因子，也不能因基线差而豁免候选绝对风险门 |
| F03 | [qlib_runner.py](../src/etf_ml/backtest/qlib_runner.py) 在非调仓日记录 `not_scheduled_rebalance` 后返回，风险检查在其后；决策用的高水位也在该路径更新 | 日级风险检查、高水位维护、估值时点和可执行时间必须共同诊断 |
| F04 | [first_loop.py](../src/etf_ml/research/first_loop.py) 的 `closure_evidence` 将 `formal_g0_passed`、`holdout_evaluated`、`investment_accepted` 固定为 false | 顶层 false 不能单独定位数据失败；数据资格须读取独立证据，旧报告不能自动改为通过 |
| F05 | 同一完成判断要求至少3个完成且付费的调用；当前 `execute_first_loop` 也要求 unlimited 和单 trial | 成熟公式的确定性离线执行需要独立完成标准，不能伪造付费调用来复用入口 |
| F06 | [selection.py](../src/etf_ml/research/selection.py) 已按折汇总种子，按实际配对/消融/风险/压力给出 accepted、rejected、inconclusive 或 failed | 复用正式判定器；修复报告状态不改变三个既有因子的 rejected |
| F07 | [验证配置](../configs/validation/walk_forward.yaml) 为 `holdout_independent: false` | 即使留出日期晚于训练，也须审计访问/选择历史，才能讨论独立确认 |
| F08 | 本机 `Alpha360DL.get_feature_config()` 返回360列：CLOSE、OPEN、HIGH、LOW、VWAP、VOLUME各60列 | 文档17原先的 close 300 已纠正；公式中的字段引用次数不等于输出特征数 |
| F09 | 当前研究面板具有标准化 OHLC、股数成交量、货币成交额，缺少独立 VWAP、PIT 成分持仓和财务输入 | 首批限定直接 ETF 价量公式；VWAP 推导和成分穿透必须分别验证 |
| F10 | v2 Alpha101 正式campaign已在冻结快照上完成5/5正式评估，#40/#44/#14/#3/#55全部rejected；每项15个折/seed行，0项工程失败，0次provider调用、未读holdout，见[实施台账终态审计](18-implementation-evidence.md#v2-alpha101-正式campaign终态补记2026-09-26) | 验证了离线因子导入→筛选→正式配对→判定的可运行链路，但当前池中这5个候选未达到风险、增量、消融、成本等联合门槛；不能从中挑选“相对最好者”替代合格因子 |

事实基于2026-09-26的当前工作树、R05诊断及v2正式run。工作树含既有未提交修改，后续验收需记录实际源码 hash，不能仅记录 Git HEAD。原始工件不会因本文件的解释而改变。

## 3. 实施路线与工作包

| 阶段 | 工作包 | 交付物 | 通过后进入 |
| --- | --- | --- | --- |
| M0：可信状态 | W01 状态/证据契约；W02 日级风险诊断；W07 旧 campaign 未结项审计 | 资格状态表、风险事件报告、未结项审计 | 可以解释当前阻碍，安排离线筛选 |
| M1：可计算因子 | W03 版本化导入与执行；W04 离线筛选 | 首批最多20个定义的目录、数值对账、逐因子结论 | 形成预筛名单，允许0个入围 |
| M2：策略适配 | W05 期限/ETF 暴露诊断；必要时风险策略独立实验 | 诊断证据；若采用变更，则新协议和匹配基线 | 固定下一轮正式实验条件 |
| M3：受控研究 | W06 失败反馈；W07 调用/恢复审计；正式配对 | 最多5次尝试的正式结果和完整账本 | accepted 才可申请独立确认 |
| M4：独立确认 | W08 留出资格、独立验证与前瞻准备 | 候选冻结包、独立资格记录、后续 OOS 计划 | 独立验收后再评估投资准备 |

W03的公式实现可与M0诊断并行开发；正式评估必须等待匹配协议、基线和就绪检查完成。M2不要求所有探索都实施：只有诊断证据支持的策略变更才进入下一正式版本，避免同时调整因子、模型、标签、调仓和风险参数。

### W01：状态与证据契约（P0）

现有布尔字段保留兼容意义；新增版本化的阶段状态，不把未知状态直接转成 false 或 true。建议输出：

| 阶段字段（拟新增） | 状态 | 事实来源 |
| --- | --- | --- |
| `data_qualification` | passed / failed / unknown | 数据审计清单、snapshot qualification、来源完整性、时间与单位证据 |
| `factor_evaluation` | accepted / rejected / inconclusive / failed / not_run | 正式 `evaluation.json` 与配对报告 |
| `holdout_qualification` | eligible / ineligible / unknown | 区间访问/选择历史、冻结记录、协议声明 |
| `holdout_evaluation` | passed / failed / not_run | 独立执行与验收工件 |
| `prospective_evaluation` | accumulating / passed / failed / not_started | 前瞻数据收据、冻结版本与运行账本 |
| `investment_readiness` | ready / not_ready / unknown | 完整研究、独立确认、运营与发布证据 |

每个阶段携带 `reason_codes`、证据路径/hash、评价时间和 schema version。真实已知失败优先报告失败；证据缺失报告 unknown/inconclusive，不能通过重新命名消除失败。

先追踪旧 `formal_g0_passed` 的定义和生成链路：顶层常量、快照内资格、来源完整性分别检查。合成“完整通过”“真实失败”“证据缺项”三类夹具验证可达性；真实数据没有完整证据时继续保持未确认。

拟修改：[first_loop.py](../src/etf_ml/research/first_loop.py)、[readiness.py](../src/etf_ml/research/readiness.py)、[contracts.py](../src/etf_ml/contracts.py)及报告消费端。迁移采用新报告版本与旧字段映射；旧 run 文件只读。

### W02：基线风险归因与可选风险版本（P0）

复用 [diagnostics.py](../src/etf_ml/research/diagnostics.py) 已有日级归因以及交易账本，交付每折/每seed的风险事件表：可见行情时点、连续高水位、权益/现金/应收、回撤、是否检查、触发、下单、最早可成交日、实际成交和失败原因，分别记录基线与候选。

重点核实三件事：非调仓日漏检是否造成延迟；仅决策时更新的 high-water mark 是否与日级账本回撤一致；风险判断用的当日开盘估值与执行成交假设是否具有可实现的时间先后。归因缺证据时输出 partial。

诊断后建议先比较单一变更版本：保留原有选股/常规调仓，增加每日风险检查。风险检查无需依赖当天存在新预测；不得使用当日收盘数据决定当日开盘成交。触发和重新入场规则一起冻结，并检验停牌、跌停、流动性不足、应收现金和跳空。

预防性降低暴露、波动目标、风险触发缓冲等属于后续独立方案，首轮不网格搜索。日级检查也不保证12%硬上限。若采用新策略，生成新的 portfolio/protocol identity 并重建匹配 baseline；历史候选仅可作为开发参考，不能继承原分数充当新策略证据。

验收：每个超限事件均有可追溯原因或明确缺项；账本权益和收益可对账；模拟路径中能正确复现检查、触发和合法执行顺序；旧协议重放结果保持原有含义。

### W03：成熟因子注册与确定性执行（P1）

先接入 Alpha101 中直接 ETF OHLCV 可支持的子集，首批最多20个唯一定义；具体公式ID由逐公式字段/算子审计产生，不提前声称已支持。选择兼顾趋势、反转、量价、波动/下行等机制，并如实记录源公式不覆盖某类机制的情况。GTJA、供应商数据和成分穿透按文档17的前提暂缓。

优先扩展 [context.py](../src/etf_ml/research/context.py)、[factor_identity.py](../src/etf_ml/research/factor_identity.py)、现有registry和因子引擎。仅在现有模块无法承载时增加小型目录/批处理模块，不另建平行研究平台。

每个定义注册原公式/版本/许可依据、字段映射、算子语义、复权/单位、窗口、方向、缺失策略、适用池、available_at和来源hash。复杂表达式使用受限算子实现，禁止对导入文本直接 `eval`。数学等价只在可证明的规范化规则内判定；高相关不等于同一公式。

数值测试涵盖排名并列、时序秩方向、标准差自由度、相关系数恒定输入、`decay_linear` 权重方向、窗口含端点、除零、NaN/Inf、缺交易日和最小有效样本数。至少有独立手算小样例，避免用同一实现生成“预期值”。

VWAP先由标准化 `amount_currency/volume_shares` 核验，检查供应商成交额/量定义、零成交、raw high/low区间与异常；若用于复权价格公式，先明确相同复权基准。若暂不支持VWAP，可登记“Alpha360去VWAP的300列派生组”，不得称为完整Alpha360。完整配置仍是六组各60列。

离线导入走确定性执行模式：零LLM调用即可完成，源公式和代码均版本化。复用因子验证器与正式 evaluator，但避开 `first_loop` 的“至少三次付费调用”完成条件。若统一报告接口，用显式执行模式选择各自必需证据；不能删除真实LLM模式的调用核验来掩盖缺失。

### W04：分阶段筛选与因子效用判断（P1）

固定一次离线预筛 manifest，先限定最多20个定义，包括实际参与选择的方向/参数变体；新增变体计入总数。Alpha360组实验另计为一次组定义选择，列数和实际搜索空间须保留，不与20个Alpha公式混淆。

筛选依次输出：

1. 技术准入：字段、时点、算子、确定性、覆盖、非恒定、无Inf、索引唯一与因果测试。不支持的公式为 bypassed/quarantined，技术失败为 failed，不能写成经济无效。
2. 信息诊断：IC与ICIR是因子预测有效性的核心验证证据，报告必须按折、日期（模型评价还要按seed）展示IC/RankIC序列及汇总；汇总至少包含有效日期数、覆盖率、日IC均值与样本标准差、ICIR、RankIC均值与样本标准差、RankICIR、正值日期占比。定义为`ICIR = mean(daily IC) / sample_std(daily IC, ddof=1)`，RankICIR同理，默认不年化；样本不足2日或标准差为0时记`null`并说明原因，不得补零。不得把不同折、seed或标签期限混池计算。报告也应列覆盖、信号衰减、相关簇；标准化、去极值和残差化只在训练窗口拟合。跨分类结果仅在分类PIT证据具备时生成。
3. 条件增量：在已有20项基线特征上测增量；一个因子自身IC较好仍可能被基线覆盖。高相关可用于优先排序，未经增量/消融验证不能自动淘汰。
4. 执行诊断：预测排名 → 入选 → 权重 → 订单 → 成交 → 成本后收益。区分“排名改善但组合不变”和“换手吃掉收益”。
5. 正式候选表：最多5项，允许少于5或0项；每项附入围理由、失败风险、来源与全部试验记录。预筛只产生 shortlist，正式 accepted 由现有判定器给出。

初筛经济阈值和排名规则必须在查看该批结果前写入 manifest。用户已确认以IC/ICIR符号判定信号方向：两者为正表示正向候选；两者为负表示反向信号候选，可在训练期决定对因子取反。IC与ICIR符号不一致时方向结论为unknown，不自动择向；任一为零、缺失、样本不足或零方差均为no_signal/unknown，不得补零或改判。方向选择只能依据各折训练内层数据并在该折验证前冻结；用于选方向的数据不得再用于方向确认。

2026-09-28用户确认新的跨折规则：五折方向化后，至少三折在同一折内同时满足验证IC>0且ICIR>0，即通过信号候选门槛。不要求五折全通过，也不计算跨折平均值；模型种子不增加票数。`ComparisonProtocol.factor_signal_rule=strict_majority`及筛选manifest绑定此规则，报告保留逐折值，并列出`fold_count`、`required_pass_folds`、`passing_folds`、`failed_folds`、`unknown_folds`和汇总状态。生产五折所需票数为3；小型测试使用其冻结折数的严格多数。

零值和未知折不计通过票，且不缩小分母；已有3张通过票时信号门槛passed，否则未知折可能影响是否达标则为unknown，确定不足3票则为failed。整折缺失或重复属于实验矩阵错误，不能当作完整的五折结果；完全缺少因子信号证据不能正式接受。Alpha101训练内预筛同时保留已有增量与机制配额要求，正式accepted还须通过增量、消融、组合风险、成本与换手等门槛。

新规则只适用于新协议和新筛选身份，不追溯性改写既有run结论。使用训练内部时序切分选择方向、变体与候选；外层开发折如被反复用于选择，应标注探索性质并完整计数，不能再宣传为独立确认。

离线库筛选的候选相关簇以同一内层验证折、同一日期的横截面 Spearman 相关为基础；绝对中位相关阈值0.90、严格多数折规则预先绑定到筛选规则。簇仅用于暴露冗余，绝不自动剔除成员或改变正式评价门槛。阈值改变视为新筛选定义，必须新建run，不回写旧结果。

### W05：期限与 ETF 重复暴露（P1）

期限诊断先固定1/5/10/20交易日观察窗口以及实际月中/月末执行的信号年龄，报告信号衰减与成本后组合影响。Alpha101论文给出的持有期约0.6—6.4天，这只支持开展期限适配诊断，不能证明某个公式在本ETF池的最佳期限。[原论文](https://arxiv.org/abs/1601.00991)

凡据此改变标签/调仓并比较择优，均新增实验定义、重建标签与baseline，并重新验证purge/embargo与时间块长度覆盖最长标签重叠；不能仅将 `horizon` 改大而继续复用旧样本/基线。默认维持5日标签及原调仓，直到独立实验支持变更。

ETF重复暴露先用现有历史跟踪指数/类别映射检验数据可用性，再分析同指数多只ETF是否挤占前K名、主题集中是否解释回撤，以及类别内排序的有效截面大小。缺历史分组时明确跳过正式分组实验；不得把当前分类回填历史。

后续可单独比较“同指数代表ETF”“分组名额/暴露约束”“类别内与跨类别分开评分”，但每次只比较预先声明的少量规则。同指数代表的选择必须使用当时可见流动性等信息。新增约束有独立策略身份，不与原候选增量混算。

### W06：让失败反馈约束下一假设（P1）

复用 [prompting.py](../src/etf_ml/research/prompting.py)、[research_memory.py](../src/etf_ml/research/research_memory.py)、[feedback.py](../src/etf_ml/adapters/rdagent/feedback.py)。每张失败卡保留来源、定义hash、五折/种子、基线继承风险、候选风险增量、成本、换手、消融、信号到成交链路及证据hash。

将下一假设限定为回应一个有证据的缺口，例如“降低短时信号衰减”“避免与已有动量完全重复”“改善成本后而非毛收益”。模型可以解释和建议，正式 evaluator 继续独立决定结论。检验核心失败原因实际进入请求，不能仅证明卡片存在于磁盘。

定义重复优先缓存复用；只有技术错误进入有界代码修复，经济拒绝不得无限改窗口重试。提示词压缩保留必需证据、单位和时点约束；提供缺项说明，禁止凭缺少成交数据推断“排名没有影响”。

### W07：搜索、费用、恢复与性能（P0审计 / P1实施）

先核实当前2个 open attempts 的进程、checkpoint、请求回执和事件链。已结束的失败尝试追加审计结论并保留已占用名额；未确定调用结果记 uncertain，不能自动重发或退款式释放名额。恢复演练先在夹具上做，不对历史工件破坏性操作。

预算对象分别计量：导入定义、参数/方向尝试、正式trial、proposal、物理dispatch、传输重试、代码修复、墙钟时间和费用。正式上限仍为5次尝试；物理调用/修复/超时停止点有独立显式默认值（每trial 5 dispatch、2 repairs、campaign 8小时），值在运行manifest冻结；变更须新protocol/campaign身份。campaign deadline不会强杀已启动worker，worker返回后超时trial不提交结果。批量成熟公式预筛为零LLM调用。

当前已落地的RDAgent transport策略是每个逻辑请求最多1次物理子进程dispatch；模糊连接/响应失败记录为uncertain并禁止自动重发。dispatch intent在进入provider子进程前先落盘；每次LLM运行根有不可变策略清单，记录传输身份、物理尝试规则、每dispatch超时与输出限制。人工/供应商对账前不能把uncertain变成可重试；若后续要增加安全重试，须有可验证的供应商幂等键或明确“请求未发出”的证据，并建立新策略身份。

保留 `unlimited` 金额策略。拟采用“允许未知费用继续记账并在结束时报告”的默认提案，与现有未知费用事实相容；未知成本停机可作为可选策略，不能因字段名字是unlimited就假定调用数量无限。没有供应商真实usage时，实际tokens/cost为null，估算字段单独保存。当前10条未知费用不应估算补成实付。campaign usage摘要同时把open trial、供应商tokens缺失和费用unknown标为partial；trial提交完不代表usage完整。

缓存拆分为计算与评价两层：计算键含输入快照/研究区间/universe/定义/字段映射/算子与实现版本；评价键另含协议、模型、seed、标签、基线和环境。只用snapshot+公式文本不足以证明结果可复用。缓存命中校验hash，跨协议禁止误复用。

已有 [protocol.py](../src/etf_ml/research/protocol.py) 将源码与环境纳入身份。报告代码修改也可能使旧基线兼容性变化；应使用正式兼容性检查并建立新基线，不能关闭hash检查以节约计算。先测相同输入重复运行的耗时、缓存命中与provider调用数，再声称性能改善。

当前A19回归已直接验证snapshot、baseline、universe、label horizon、model、seed、environment和source hash任一变化均改变`protocol_id`，源码/环境漂移时旧protocol拒绝运行；Docker/Qlib集成验证同身份paired评价复用、损坏子工件拒绝，并对干净首跑/相同输入复用测得12.590秒/0.409秒（单次合成基准，不作通用加速外推）。没有启动第二物理环境，但运行环境漂移的身份和fail-closed合同已测。A19有计划范围内的直接测试证据；这不等于W07整体完成。详见[实施证据](18-implementation-evidence.md#a19-正式评价缓存身份回归2026-09-26)。

### W08：独立确认与前瞻准备（P2）

2026-09-26补记：已交付[文档20](20-w08-independent-confirmation-defaults.md)、新的W08准备配置和机器可读计划。六项工程默认值为累计净收益3%、超额1%、回撤12%、年化波动15%、执行成本/初始权益2%、252个有效日期；不是行业平均或实际业绩。旧区间访问历史未知时暂缓真实独立确认，`holdout_independent=false`不改。准备子项完成，效果验收仍未完成；本段取代后文“所有配置六项仍待填”的当前状态，不改变旧报告和其原配置。

为当前 `holdout_independent=false` 查明原因：配置默认值、真实访问、人工看过结果、已有回测或择优使用，分别记录证据。不能只翻转布尔字段。历史使用不清楚时保持 unknown/ineligible；需要新的未参与选择的窗口或候选冻结后的前瞻样本。

只读研究审计可接收人工审阅的`holdout-access-audit-v1`来源文件；审计输入绑定日期边界、访问/择优历史、审核人/时间和来源SHA256。资格审计不自动生成“未访问”的证明，也不消费行情值；freeze/evaluate入口只服务实际冻结包，holdout评估仍受一次性区间claim保护。

候选冻结包包含因子定义/代码/数据与universe规则、处理器、模型、标签、调仓、成本、风险、协议hash，以及完整搜索谱系。预先定义独立验收统计、样本长度、停止条件和一次性查看规则。失败后的修改成为新候选，不能继续使用相同留出声称独立。

多重试验台账是透明化基础，不等于统计校正。当前时间块bootstrap诊断不自动具备搜索后显著性含义；如采用多重检验/选择偏差方法，应单独论证时间依赖、候选相关性与样本长度。首版优先使用有界预注册选择和真实独立确认，不新增一个未经验证的“校正分数”。

## 4. 代码与工件映射

以下为拟修改位置和交付契约，不预设所有新文件都必须创建。

| 工作包 | 优先复用/修改 | 必交付工件（拟定） |
| --- | --- | --- |
| W01 | `research/first_loop.py`、`research/readiness.py`、`contracts.py` | `qualification_report.json`、状态迁移说明 |
| W02 | `backtest/qlib_runner.py`、`research/diagnostics.py`、`research/paired.py` | `risk_attribution_report.json`、逐日事件证据 |
| W03 | `research/context.py`、`factor_identity.py`、`factor_engine.py`、现有registry | `source_manifest.json`、`factor_catalog.json`、数值对账记录 |
| W04 | `research/diagnostics.py`、`selection.py`、`paired.py`；必要的小型批处理入口 | `screening_manifest.json`、`screening_report.json`、`shortlist.json`；逐折/seed IC与ICIR及其统计口径，主报告内嵌汇总或带hash链接 |
| W05 | `contracts.py`、标签/数据集模块、`backtest/qlib_runner.py`、`research/library.py`、CLI | `horizon_diagnostics.json`、`exposure_diagnostics.json`、四期限paired结果及各自匹配baseline |
| W06 | `adapters/rdagent/feedback.py`、`research/prompting.py`、`research_memory.py` | 反馈覆盖报告、请求核心证据对账 |
| W07 | `research/campaign.py`、`controller.py`、`llm.py`、`session.py` | campaign审计、usage摘要、恢复/缓存验证记录 |
| W08 | 现有holdout/冻结/版本管理入口 | `holdout_qualification.json`、候选冻结包、独立确认方案 |

所有新工件包含schema version、source hashes、生成配置、状态/原因和证据路径。保存到新的研究/验收目录；禁止在既有run下覆盖同名结果。兼容旧字段的读取测试先于发布，unknown不能被消费端默认转成passed。

## 5. 验收矩阵

| 编号 | 工作包 | 必测场景 | 通过标准 |
| --- | --- | --- | --- |
| A01 | W01 | 数据通过/失败/缺项；因子独立接受/拒绝 | 各阶段状态按各自证据生成；不会相互覆盖 |
| A02 | W01 | 旧report布尔字段与新状态读取 | 兼容消费端；旧工件hash不变；常量false不被猜成真实原因 |
| A03 | W02 | 非调仓日触发、缺预测、高水位变化 | 日级归因完整；可选新风险版可在无新预测时评估持仓风险 |
| A04 | W02 | 当日开盘/收盘、跳空、停牌、跌停 | 决策与成交无前视；受限成交真实反映，不承诺回撤绝不超限 |
| A05 | W02 | 账本/应收/现金与风险事件对账 | 同一收益账本；缺项为partial；基线与候选归因可区分 |
| A06 | W03 | 公式手算/独立实现，算子边界 | 定义一致且误差容限预声明；无静默近似或不受限eval |
| A07 | W03 | volume/amount/VWAP/复权及零成交 | 单位对账通过；缺VWAP的派生组明确命名和列数 |
| A08 | W03 | 未来扰动、截断、instrument置换、PIT | 历史输出不随未来值改变；合法横截面置换等变 |
| A09 | W03 | 零LLM调用的确定性库因子 | 真实完成计算/验证/评估；不伪造调用，LLM模式仍核验调用证据 |
| A10 | W04 | 全常量、覆盖不足、重复索引、非有限输出 | 明确技术拒绝/绕开原因；分母和暖机期口径可审计 |
| A11 | W04 | 高相关但有增量、IC改善但成交不变 | 不误把相关当等价；信息/组合/经济层结论分别有证据 |
| A12 | W04/W08 | 训练拟合、开发择优、跨区间读取 | 无处理器拟合泄漏；尝试完整登记；预筛不能读取holdout |
| A13 | W05 | 更长标签与重叠样本 | purge/embargo、时间块和样本hash同步更新；旧baseline拒绝混用 |
| A14 | W05 | 同指数多ETF、历史分类缺失 | 曝露计数正确；当前分类不回填历史；缺项明确绕开 |
| A15 | W06 | 大反馈卡/预算边界/缺执行工件 | 必需失败原因实际送达；缺证据为unknown；不无界经济修复 |
| A16 | W07 | 并发第5/第6次尝试、已耗尽campaign | 上限原子执行；第6次不分派；失败已用名额不被清零 |
| A17 | W07 | 响应不明、强制中断、幂等恢复 | 不重复付费分派；不重复事件；未决记录完整保留 |
| A18 | W07 | 全部/部分unknown、零LLM、unlimited | actual值按证据为null/实测；估算独立；unlimited不自动修改 |
| A19 | W07 | 数据/算子/代码/环境/协议变化 | 缓存正确失效；基线身份检查有效；性能数字来自同条件测量 |
| A20 | W04 | 五折三seed与消融/2x成本/12%风险/-3%压力线 | accepted/rejected/inconclusive/failed夹具结果正确；边界单位一致 |
| A21 | W08 | holdout访问历史未知或曾参与选择 | 不得声称独立通过；冻结前后身份与访问日志可核验 |
| A22 | 全部 | dirty worktree、原始数据、旧run/快照 | 验收包记录实际源码hash；不覆盖用户修改；保护对象hash不变 |

执行层次：改动模块单测 → 有意义的组合/数值集成 → 必要时Docker隔离与ReplayTransport集成 → 获运行指令后真实研究。沿用现有 `test_daily_risk_attribution.py`、`test_paired_selection.py`、`test_research_campaign.py`、`test_first_loop_readiness.py`、`test_time_block_statistics.py` 等；新增覆盖只针对新行为和真实边界。

验收证据包应包括测试命令、退出码、结果、源码/配置hash、环境、数据身份、未覆盖项。历史“829 passed”等记录属于当时版本，不可直接作为新实现通过证据。本次为文档变更，仅核对证据、链接和格式，不运行研究/回测或声称新增功能测试通过。

## 6. 推荐首批范围、决策点与停止规则

推荐首批范围：完成W01、W02诊断、W03/W04至多20个因子定义的离线链路、W07未结项审计；W05先诊断；W06/W08先形成可验收契约。成熟库批算过程中无需为了满足完成条件发起LLM请求。

已确认配置继续沿用：-0.03压力收益线、12%回撤验收、5次campaign上限、unlimited金额策略。以下三个决策由前置证据形成具体方案后处理，不阻塞文档、通用实现和离线夹具：

1. 是否采用新风险执行/标签/分组策略：根据W02/W05提供逐项对照，并给出完整新协议和baseline需求。
2. 下一批预筛的具体公式名单、经济筛选规则和物理调用/修复/时限配置：在结果产生前固化；进入新正式campaign时使用新的ID并继承全局搜索谱系。
3. 独立确认区间与资格：根据真实访问记录和数据可用性确定，不能仅选择一个看起来较新的日期。

停止条件：达到批次定义上限或campaign尝试上限；真实数据/时间/单位契约失效；必要证据缺失；缓存/账本hash断裂；运行达到预声明的调用/修复/时限上限。按用户的费用政策记录未知费用，不自行添加金额硬上限。技术阻碍产生可恢复状态，经济拒绝保留最终判定；0个合格候选也是完整研究结果。

## 7. 最终交付与进度清单

2026-09-26 W07/W08 补全更新：新增只读 `prepare-research-closure`，实际留出执行与缓存复用强制要求v2人工访问审核。用户随后批准“历史费用暂不追补，未来改善采集，研究门槛不放松”，因此W07工程验收与历史费用核销分开，历史账务保留partial而不再作为研究阻碍；W08仍待访问历史、独立验收参数和accepted候选。见[操作交接及最新决定](19-w07-w08-closure-guide.md)。旧A22/r4证据保留为旧源码版本的历史验收，不作为新增源码测试证据。

- [x] W01：交付可追溯阶段状态与旧报告兼容；通过真实R05只读审计确认旧固定布尔值不会改变阶段资格。
- [x] W02：交付基线/候选日级风险归因；风险卖单意图与成交严格关联，旧记录缺项保持partial；任何风险策略变更另立版本。开盘估值和同开盘成交的先后关系、未成交时最早合法退出日仍是显式限制，不据此宣称无前视或硬回撤保证。
- [x] W03：交付版本化成熟因子目录、独立数值对账与零LLM执行；正式因子结论仍由原判定器决定。
- [x] W04：已用新screen identity对冻结snapshot的20项Alpha101定义完成离线筛选；技术通过17、未评分3，预筛拒绝8、因shortlist上限deferred 4、shortlist 5。逐折报告IC/ICIR raw/oriented与正负方向；85条有效折方向记录含23条训练反向，方向化验证53条passed/32条failed。IC/ICIR仅折级报告，不做候选级跨折聚合；shortlist五项与既有正式拒绝清单相同，0 provider调用、0新formal evaluation、未读holdout。历史screen-v2与paired结论均不改写。当前全量`tests/unit`为966 passed；完整`tests/integration`首跑100 passed、1项反馈投影断言在修复后单项复验通过（修复后未重跑整套）。实现与精确测试范围见[实施证据](18-implementation-evidence.md#w04训练内方向规则正式离线筛选2026-09-26)。
- [x] W05：已交付共同成熟样本诊断、PIT实际持仓暴露审计和`compare-library-horizons`入口（每个标签期限独立protocol/baseline，四期限不择优）。冻结快照campaign `plan18-alpha101-horizons-v1`四期限均终态完成，每期限5项候选均正式rejected、工程失败0；共20项无accepted。h=20最后`alpha101_055_etf`也rejected，理由含消融未确认、绝对风险、成本压力收益/风险、无多数折增益及seed不稳定；其15折/seed候选相对baseline收益、回撤和换手差值均为0。h=20前两项#40/#44各15行配对且收益增量1正/2负/12零，#44另有换手恶化；#14/#3/#55均无组合增量。多个候选触发12%回撤或2倍成本-3%压力线。screen-v2仍冻结为published orientation，未执行IC/ICIR反向选择，旧结论不追溯改判；开发结果不择优，未读取holdout。详见[实施证据](18-implementation-evidence.md#2026-09-26-w05-多期限成本后组合评估入口)及终态补记。
- [x] W06：交付失败反馈覆盖审计、历史卡只读重建、失败反馈送达及下一假设约束测试（范围限显式登记的研究源；未评价失败项保持不适用/未知）。
- [x] W07（工程验收，历史账务不等于核销完成）：既有cap并发、调用/恢复、缓存身份与性能证据保留。按用户批准，历史10条已记录调用的费用/usage保持unknown，不要求追补；R02/R04终态失败的名额保留，原campaign 5/5已耗尽。新增campaign事件链绑定的费用政策、只读派生视图，以及未来供应商响应usage/request ID采集和一次底层调用保护；禁止用估算冒充实付或实际tokens。旧`usage_audit_status=partial`不改写。新通路通过离线RDAgent联调，未发起真实付费请求，不声称真实供应商usage覆盖率已达100%；精确测试范围见实施证据最新补记。后续正式研究必须新protocol/匹配baseline，W08独立确认仍未通过。
- [ ] W08：冻结模型、一次性holdout执行及可选人工访问历史审计接入已通过合成集成（`test_holdout_pipeline.py`：2 passed，含失败重试、提交中断恢复及同一版本/区间防重复消费）；最新当前源码复核仍判R05候选rejected、holdout资格`ineligible`（`holdout_independence_not_declared`）、holdout evaluation `not_run`，data qualification `unknown`。只有人工审阅的真实访问历史、正式接受候选及尚未消费的新holdout窗口齐备后，才可申请真实确认。当前不读取正式holdout，W08仍未完成；见W07/W08审计`artifacts/research_audits/plan18-r05-w07-audit-20260926-r4`。

A22当前阶段验收包已生成，包含source code hash、dirty worktree状态、Tushare配置/snapshot/baseline身份、R05只读保护审计、全量单测JUnit和paired Docker/Qlib缓存计时JUnit；结果状态为partial，明确保留W07/W08未闭合边界。Manifest路径`artifacts/research_audits/plan18-a22-evidence-20260926/evidence-manifest.json`，SHA256 `71c669fe33f8cd22b840a492e50742f1007dcd6cbc9b90dfb2344521b2c726e9`。它是阶段证据包，不是整体验收通过声明。

每次阶段交付须报告：新增实现、通过测试、缺项、所有候选去向和下一依赖。最终逐因子结果表至少含：来源/定义hash、数据适配、技术状态、预筛状态、正式结论、失败/绕开原因、证据路径、独立资格与投资准备状态。

当前结论保持：R01 `vwc_location_20`、R03 `trend_clarity_60`、R05 `up_volume_share_60` 均为正式拒绝；没有已确认投资就绪因子。新规划的完成意味着下一轮工作有具体范围与验收方法，不意味着已经找到有效因子。

### 2026-09-28 campaign机制配额补充

正式RDAgent campaign可显式启用不可变`five_factor_v1`机制计划：`trend_momentum`、`reversal`、`volume_price`、`volatility_risk_adjusted`、`orthogonal_new`各最多一次。trial开始时在campaign事件链锁内分配槽位，失败/中断也保留已占槽位；下一折的prompt task contract要求精确匹配`research_group`，生成和一次受限修复后再校验，不匹配不得进入coder。checkpoint包含相同分配及上下文hash，恢复时与账本交叉校验。旧campaign及离线库评测默认不启用该计划。

正式入口使用新campaign身份并同时传`--mechanism-plan five_factor_v1`；readiness也必须传同一参数。该配额是候选生成多样性约束，不放宽IC/ICIR、配对增量、消融、成本、风险或换手门槛。该机制仅让候选来自预先分散的假设类型，不保证候选质量或找到合格因子。
