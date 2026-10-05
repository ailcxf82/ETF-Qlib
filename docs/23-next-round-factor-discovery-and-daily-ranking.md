# 下一轮因子发现与 ETF 研究排名

日期：2026-09-29 ｜ 状态：研究工程已完成首轮离线预筛；正式验证阻塞

## 目标

在不调整因子验收门槛的前提下，发现具有稳定增量价值、相互不过度重复的信号候选；通过验证后，接入每日 ETF 研究排名。目标是形成 3–5 个候选并推动至少一个通过完整验收，结果允许为零。

沿用：方向仅按训练期确定；五折至少三折同时 IC>0、ICIR>0 方可入围；压力累计超额收益不低于 -0.03；绝对最大回撤验收线 12%；五次正式尝试封顶，未知供应商费用记 `cost_unknown`。种子、配对增量、消融、换手、数据和独立留出门槛继续有效。

## 当前证据与首个实施变更

2026-09-28 的趋势效率、资金流和隔夜跳空复测分别在 3/5、4/5、4/5 折通过方向化 IC/ICIR 门槛；三者完整验收均为 rejected。共同拒绝项包含绝对风险、压力风险/收益、种子稳定性，且增量/组消融未证明充分贡献。

之前 `portfolio.risk` 同时充当回测触发值和最大回撤验收上限。当前源码已增加独立 `portfolio.max_drawdown_limit`：正式研究配置把触发值设为 8%，回撤验收仍为 12%；协议身份、基线、配对及报告会携带此配置。较早触发是待验证的实验假设，不代表必然把实现回撤控制在 12% 以内。年度波动率模式仍由 `portfolio.risk` 控制。

触及代码：`contracts.py`、`qlib_runner.py`、`selection.py`、`finalize.py`、`holdout.py`、`audit.py`、`session.py`、RDAgent 反馈，以及对应配置和测试。历史工件不改写。

## 2026-09-29 首轮预筛与基线结果

与当前源码/配置匹配的基线 `tushare-formal-early-trigger-baseline-20260929` 已完成：10 个 fold/seed 模型运行及对应辅助回测产物，源码哈希 `24c2b3368c8d19a300f61b023bd132a6cc2b6c37a662d68eacb93a245929ece1`，快照仍为 `8b384b5d9f8fa10d3d7b6aebbf11fd9463dc76cc9fe203d38dfc6f5ad4322831`，基线报告 SHA-256 `de173f6cc2fe270564836c2fe4ae247928f8d73894a7fe9a3ee1c9853719205f`。基线终态 `completed` 不等同 G0：报告仍为 `formal_g0_passed=false`。

离线 Alpha101 筛选 `alpha101-early-trigger-screen-20260929` 已评估 20/20 个定义，仅输出 3 个 shortlist：`alpha101_040_etf`（volatility/risk-adjusted）、`alpha101_044_etf`（volume/price）、`alpha101_014_etf`（reversal）。三者在筛选期内的增量 RankIC 均为 5/5 折正；方向化 IC/ICIR 门槛分别为 4/5、5/5、3/5 折。预筛 shortlist 不代表正式接受。其相关性诊断只形成两组高相关簇（006/014、033/101），规则为 3 折以上绝对中位日 Spearman >0.9，且诊断不自动淘汰；入围的 040/044/014 分属不同机制槽位。

以下是 shortlist 的折级因子信号指标；方向均只由对应训练窗确定，表中 RankIC 为方向化后的验证值。有效日数按 A–E 折分别为 140/164/188/212/237，覆盖率 040 全折为 1.000、044 为 0.993–0.994、014 全折为 1.000。

| 因子 | 折 | IC | ICIR | RankIC | IC/ICIR 同时为正 |
| --- | --- | ---: | ---: | ---: | :---: |
| 040 | A | 0.0291 | 0.2298 | 0.0321 | 是 |
| 040 | B | 0.0045 | 0.0259 | 0.0195 | 是 |
| 040 | C | -0.0002 | -0.0015 | 0.0119 | 否 |
| 040 | D | 0.0127 | 0.1008 | 0.0222 | 是 |
| 040 | E | 0.0173 | 0.1095 | 0.0201 | 是 |
| 044 | A | 0.0138 | 0.1518 | 0.0207 | 是 |
| 044 | B | 0.0086 | 0.0966 | 0.0117 | 是 |
| 044 | C | 0.0101 | 0.1070 | 0.0166 | 是 |
| 044 | D | 0.0196 | 0.2195 | 0.0248 | 是 |
| 044 | E | 0.0043 | 0.0452 | 0.0093 | 是 |
| 014 | A | 0.0348 | 0.3020 | 0.0398 | 是 |
| 014 | B | 0.0096 | 0.0725 | 0.0114 | 是 |
| 014 | C | -0.0023 | -0.0189 | -0.0019 | 否 |
| 014 | D | -0.0008 | -0.0066 | 0.0020 | 否 |
| 014 | E | 0.0087 | 0.0570 | 0.0101 | 是 |

筛选工件同时保留每日 IC 标准差和正 IC 日占比，可用于描述折内波动；它没有提供区块自助法置信区间。正式候选报告应补上保持时间依赖的区块不确定性区间，且不得以这里的描述统计替代完整验收。

期限诊断仍支持按既定五日标签开展正式检验，而不足以单凭预筛切换期限：040 的 20 日中位 RankIC 高于五日但仅 4/5 折为正；044 在 10/20 日均为 5/5 折为正；014 的长周期折间一致性更弱。修改预测期限会形成新协议并消耗正式尝试名额，故本轮不据此改标签/调仓。筛选报告记录了折级候选/基线预测统计、覆盖和每日 IC 离散度；本阶段未生成完整接受用的区块不确定性区间，须在正式候选报告补齐，不能将这些诊断冒充完整验收。

筛选状态为 `completed`，正式评估 0 次、外部调用 0 次、未评估 holdout。工件哈希：筛选报告 `89b4612b0009c12014c3002084ca92357ecca53ee7cf68b86785bb33b352f615`，shortlist `10e3b0c1042e6fccac544f3f066e5e5b5bd222967a5e89bb006087099a25b80ac`，manifest `47fd3dc5ca7ce756a9341235ed35cc7fdab5e7724f4400489f67e8e93d504fce`。完整逐折数据保留在 `artifacts/library_screening/alpha101-early-trigger-screen-20260929/`。

另对数据源请求缓存做了只读覆盖核对：冻结 manifest 中的原始 `instruments/all.txt` SHA-256 与只读源文件相同；1769 个原始 ETF instrument 对 `fund_div`、`fund_adj` 各有 1769 份唯一、无缺失/额外/重复、带 raw hash 的请求收据。两接口收据 `rows` 合计分别为 1,906 和 1,344,565，与事件报告和调整因子材料的输入规模相符。这证明请求/缓存覆盖及材料血缘，不证明供应商返回内容无漏项：所有收据的 `source_completeness_verified` 仍为 false，事件与因子组装报告均保留 `g0_passed=false`。当前调整一致性检查只验证有事件且相邻报价存在的日期，代码明确提示时间缺口和来源完整性需另有覆盖证据；因此不能据完整请求数把数据资格升级为通过。

## 已有能力，优先复用

- `research/library.py` 已支持最多 20 个 Alpha101 定义的确定性筛选、最多 5 个候选短名单、训练期方向选择、逐折 IC/ICIR 和期限诊断、机制差异优先及相关性审查。
- `research/selection.py` 已执行逐折/种子配对、增量、风险、成本压力、稳定性与消融判定。
- `operations/daily.py` 已实现每日信号入口，但要求匹配的冻结模型、当天完整快照、行情/账户收据和交易日后收盘时点。

## 阶段与验收

1. 风险参数拆分：触发值与事后验收值能分别配置；触发 8% 时报告显示验收线 12%；选择、消融、冻结审查和独立确认始终使用 12% 验收线。
2. 测试并冻结源码：风险单测、Qlib 风险时序集成、配对选择、最终冻结审查、独立确认和完整单测通过；记录源码、环境、配置身份。
3. 匹配基线与期限诊断：在冻结研究快照上重建与新代码/配置匹配的基线，复用 1/5/10/20 日诊断；若改期限或调仓，只做独立新协议和基线。
4. 因子筛选：冻结最多 20 个定义的预筛清单（参数变体也计数），最多输出 5 个非重复机制候选。每项披露定义哈希、方向来源、折级 IC/ICIR/RankIC、有效样本、区块不确定性、覆盖与期限衰减。
5. 正式验证：新 campaign 最多五次，候选复测及组合实验占名额；先通过只读 readiness、容器镜像、费用台账和匹配基线检查。失败或未完成调用保留在账本中，不释放名额。
6. 独立确认：候选通过正式研究验收后冻结模型，检查访问历史和留出资格；不具备资格则登记前瞻起点和终点，结果缺样本为 inconclusive。
7. 每日排名：仅接受最新完整交易日、PIT 股票池、覆盖门槛和已冻结模型；输出最多 10 个研究观察对象，允许为空。展示截止时间、模型/期限、因子贡献、同类与相关性、风险及数据质量状态；预测分数不能直接作为上涨概率或收益承诺。

## 当前阻塞与证据边界

只读 readiness 在 2026-09-29 返回 `runtime_unavailable`：Docker Linux daemon pipe 不存在，固定镜像不可校验。当前复核仍无法连接 Docker daemon（`com.docker.service` 为 stopped）。冻结快照仍可用，含 1480 个研究 instrument，但数据资格为 unknown；请求缓存覆盖已核对，调整源与事件源的供应商返回完整性仍未认证，G0 未通过。快照 cutoff 为 2026-09-04，不能用于 2026-09-29 之后的当日排名。历史留出独立性未证明。当前没有 accepted 冻结模型，因此每日排名不可发布。

此前提供的持仓代码在该快照的面板与元数据中都有记录；按首期 `domestic_equity` 范围，515070.SH、515750.SH、560780.SH 纳入范围，513380.SH 与 511360.SH 标记 `outside_or_unclassified`，不能据当前模型清单评价。此为代码覆盖检查，不代表持仓数据是最新账户状态。

2026-09-29 的整套指定回归已闭合：1087 项通过、1 项失败、13 条警告；唯一失败为 `test_actual_docker_factor_fixed_qlib_pair_stress_ablation_and_verified_reuse`，因 Docker Linux daemon pipe 不存在而在运行时预检失败。该失败仍是环境阻塞，不能记为通过。风险/选择/最终冻结/独立验收用例通过。8%/12% 新配置匹配基线现已完成，但 G0 仍失败/未知。不能以局部测试结果、基线通过或 Alpha101 筛选代替因子研究接受、独立确认或投资准备。

## 命令与运行策略

配置：`configs/data/tushare_formal_first_loop.yaml`。固定快照：`artifacts/formal_tushare/data/8b384b5d9f8fa10d3d7b6aebbf11fd9463dc76cc9fe203d38dfc6f5ad4322831`。新源码需要新基线和新 run/campaign 身份；旧 snapshot、baseline、campaign 及 rejection artifact 保留。campaign 上限以新冻结账本为准，不能继续使用耗尽的旧 campaign。

Docker 与资格问题解除后，先跑完整测试和 readiness，再确认已生成的匹配基线与 20 项离线筛选仍对应当前源码/配置；身份变化则重建受影响工件。通过安全门后，对 shortlist 逐个运行正式配对验证，campaign 正式尝试累计最多五次。任一安全门未满足即保留证据并报告具体 blocker，不自动扩展额度，也不读取独立留出数值用于调参。

## 2026-09-29 运行后补记

本补记取代上文“Docker 当前不可用”的运行时状态，不改写当时 readiness 的历史结果。Docker Desktop 当前已返回 Linux 引擎；原先因 daemon pipe 不存在而失败的 Docker 集成测试尚待在负载允许时重跑，不能因此把旧失败记为通过。

正式 campaign `tushare-alpha101-early-trigger-20260929-c3` 的封顶为 5 次、8 小时；截至本次复核，trial index 0 正在运行，故已占用 1 个名额，不能另行并发或重复启动。RDAgent 候选 `intraday_pressure_reversal_20` 处于 `proposed`，配对报告尚在运行；当前仍在 seed 44 的基线评估阶段。研究使用既有冻结快照与匹配基线，未修改二者。供应商请求虽返回 token 用量，但账单金额/来源不可用，仍按 `cost_unknown` 记账。

数据新鲜度复核发现：`artifacts/formal_tushare/data/` 仅有快照 `8b384b5d9f8fa10d3d7b6aebbf11fd9463dc76cc9fe203d38dfc6f5ad4322831`，其 manifest cutoff 为 2026-09-04；只读 Qlib 源的 `calendars/day.txt` 最后一日也为 2026-09-04，文件未显示更新到该日之后。因此当前没有可验证的最新完整交易日输入；必须先完成后续行情/日历/补充数据的获取、快照构建与 G0/完整性审核，且不得改写旧快照，才可准备当日排名。现阶段没有 accepted 冻结模型，daily ranking 仍不得发布。

该正式 run 的 `formal_g0_passed=false`，campaign 有一条开放中的 trial；因此目前只有运行中证据，没有研究接受、独立留出或投资就绪结论。下一个安全动作是继续等待该运行终态、审阅其完整配对/消融/风险/换手/成本证据，再在剩余 campaign 名额内作决定；不因 Docker 已启动而跳过 G0 或放宽任何研究门槛。

## 2026-09-29 campaign 闭环与 Docker 恢复验收

本节为上述运行中补记的终态更正：最后一个 RDAgent run `tushare-c3-rdagent-last-attempt-20260929` 已正常结束，campaign `tushare-alpha101-early-trigger-20260929-c3` 最终 **5/5 次已完成、0 个开放尝试**，账本共 19 个事件，链头为 `cfbaa4fc574cc3426e6294f19e38c5f903d3a6a1a1be7ca4d98f4166c3604ddb`。耗时 12,424 秒，未超 8 小时上限。旧 D 盘账本保留原状；本轮的可继续核对副本位于 `E:\ETF-Qlib-campaign-c3\artifacts\research_campaigns\tushare-alpha101-early-trigger-20260929-c3\`，readiness 以该副本确认 5/5 耗尽。不得继续向此 campaign 提交。

最后候选 `overnight_momentum_20` 正式 `rejected`。方向化 IC/ICIR 硬门槛为 4/5 折通过（A、B、D、E；C 未通过），达到了用户设定的方向候选门槛，但不是正式因子接受：15 个折×seed 评估中，12 个 2×成本压力超额收益低于 -3%，3 个绝对最大回撤超过 12%；同时触发 `ablation_not_confirmed`、`cost_pressure_return_limit`、`cost_pressure_risk_limit`、`drawdown_deterioration`、`no_majority_fold_increment`、`seed_instability`、`turnover_deterioration` 等拒绝项。B/C/D 的 2×成本最差超额收益分别为 -10.91%/-22.96%/-9.00%；D 折最大回撤 17.50%。所以这轮没有“方向信号已通过、策略收益也通过”的候选，原成本/风险/换手门槛不应放宽。

正式报告：`E:\ETF-Qlib-campaign-c3\artifacts\runs\tushare-c3-rdagent-last-attempt-20260929\first_loop_report.json`；资格报告：同目录 `qualification_report.json`；末候选逐折/seed 配对评估：`research\paired\runs\etf-5e144cde40e913328b0fa7c3-overnight_momentum_20\evaluation.json`，候选与消融细节在同目录 `candidate_report.json`、`ablation_report.json`。末候选 evaluation SHA-256 为 `b6e1a9ec4e843e70d42707d0faf1c2acdae1e227fa022eaa540f472381d030b9`，candidate report 为 `3b4c99f4069d3c2edb63e1f8490d129d6d06846b17d81125505eafab0e86a7cb`。本 run 明确记录 `formal_g0_passed=false`、`holdout_evaluated=false`、`investment_accepted=false`。G0 未知项仍为事件源与复权源返回完整性；snapshot cutoff 仍为 2026-09-04，不能据此输出当前 ETF 排名。

所有 provider usage 收据均已采集，但整个 campaign 有 6 次费用金额未知的调用（其中末 run 3 次），readiness 报告 `unknown_cost_calls=6`、usage 覆盖 100%、`usage_audit_status=partial`。`cost_known_subtotal=0` 不是费用为零；须保留 `cost_unknown`，不得推算或虚构美元费用。离线 Alpha101 筛选无外部调用。

Docker 恢复后的只读 readiness 核验：固定镜像通过，基线 `tushare-r8-signal-report-baseline-20260929-e` 可复用，数据资格仍 `unknown`；当前 readiness 因 `campaign_exhausted` 返回 `blocked`，而非容器或基线问题。此前唯一 Docker 集成失败已用临时隔离工件重跑：`test_actual_docker_factor_fixed_qlib_pair_stress_ablation_and_verified_reuse` **1 passed，28 warnings，37.01 秒**。加上已完成的 85 项定向测试，这补齐 Docker 运行证据，但不升级 G0、因子接受或投资状态。

### 判断与下一轮优化方向

这轮最重要的诊断是：在保持原硬门槛时，筛选出的 Alpha101 因子及 RDAgent 生成候选能在若干折产生正向预测排序/IC，却没有可靠地转化成跨 seed 的净策略增量；失败集中在压力期收益、回撤、seed 稳定、消融贡献及换手，而非 Docker、基线复用或试验执行故障。下一轮应优先修复“信号到可执行组合”的验证链，不要先扩大搜索量或调低门槛：

1. 先解决真实事件与复权数据的来源完整性证据，并获取 cutoff 晚于 2026-09-04 的新鲜快照；重新运行资格审核。旧 snapshot/baseline/campaign 不改写。
2. 若新有限 campaign 获明确授权，再冻结独立 campaign ID、试验数与费用政策，按机制槽位先做 20 项以内无费用预筛；候选 shortlist 优先排除已证实脆弱/高度冗余的机制。
3. 对每个信号同步报告横截面 IC/ICIR 与实际组合：折×seed 的净收益增量分布、持仓/成交重合、换手、2×费用压力、回撤及消融。重点诊断信号虽正但组合持仓/权重/成交不变或高度重合的情形；不以单个好折或正 RankIC 替代净增量验收。
4. 只在不看 holdout 的开发窗内比较预先声明的持仓缓冲/换手抑制/风险预算消融，先用小型合成/隔离 Qlib 集成验证机制，再冻结单一规则做正式候选；任何组合/期限变动必须纳入新协议和匹配基线。
5. 通过数据 G0 和完整正式门槛后才冻结候选并准备独立确认；只有当最新完整交易日、PIT 范围、模型冻结与数据资格均通过时，才考虑生成研究排名。当前不满足这些条件。

新 campaign 不能沿用已耗尽的 c3，也不应由自动流程自行扩额；须先处理 G0 缺口，并按新的有限范围取得用户授权后再启动。持仓代码仍只代表用户曾提供的标的范围，本文件不构成买卖建议。
