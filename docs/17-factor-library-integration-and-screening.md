# 因子库接入与筛选验证方案

版本：1.1 ｜日期：2026-09-25 ｜状态：工程方案与现有证据盘点；尚未接入外部因子库

勘误：本版纠正Alpha360输出列数、campaign计数及G0状态解释；跨模块实施顺序与验收见 [18 整体优化规划](18-integrated-factor-research-optimization-plan.md)。

## 1. 结论摘要

成熟因子可以作为研究假设和可复现公式来源，但不能因为“成熟”或来自知名因子库就直接进入实盘候选。每个因子仍须完成公式/字段映射、时点与数据语义审计、无泄漏测试、增量评估、成本压力、风险、换手和独立确认。当前正式协议仍是唯一准入标准；本方案不改写已冻结门槛。

基于当前仓库、正式数据快照和一个campaign内已登记的五次生成尝试：

- **适合进入下一阶段离线筛选的来源**：WorldQuant Alpha101 中可由当前 ETF OHLCV 字段无歧义实现的子集；Qlib Alpha360 的价量特征组在补齐/审计 VWAP、明确 ETF universe 与字段单位后可进入离线筛选。它们目前都不是实盘候选，也没有声称已在本地正式测试。
- **目前可列入实盘候选的因子：无。**已完成因子均未获 formal accepted，holdout 未评估，投资验收为 false。顶层 `formal_g0_passed=false` 在首轮报告汇总中为固定值，不能单凭它认定具体数据失败原因或数据已通过；需独立审计snapshot资格及来源证据。
- **本次实际测试并淘汰**：`vwc_location_20`、`trend_clarity_60`、`up_volume_share_60`。它们是 RDAgent 本地生成因子，不是 Alpha101、Alpha360、GTJA191 或其他外部库因子。
- **暂缓/绕开**：GTJA191 与所谓 Tonglian 424 缺少本地、版本锁定且可审计的实现/目录；Fama–French、MSCI 当前更适合作为风格/风险基准或归因数据，不是已有的 ETF 横截面因子输入；JoinQuant/RiceQuant 无已配置且许可核实的数据适配；华泰报告需先人工转成严格因子定义；加密资产因 universe 与交易日历不同而不纳入本项目。

“可以启动离线筛选”不等于“已通过筛选”；“正式研究接受”也不等于独立 holdout、前瞻 OOS 或投资准备完成。

## 2. 当前数据与代码能力边界

本次盘点以只读快照 `artifacts/formal_tushare/data/8b384b5d9f8fa10d3d7b6aebbf11fd9463dc76cc9fe203d38dfc6f5ad4322831` 和现有源码为准。快照研究面板有 1,093,068 行、1,480 个 ETF、日期 2020-01-02 至 2025-12-31，已知列为：

`adjustment_factor, raw_open, adj_open, raw_high, adj_high, raw_low, adj_low, raw_close, adj_close, volume_shares, amount_currency, return_1d, reference_close, quoted, tradable, buy_block_code, sell_block_code`

当前特征基线已使用复权 OHLC、股数成交量、人民币成交额构建 20 项特征。面板没有独立 `vwap`、成分股持仓/权重、财务数据、Fama–French 因子序列、MSCI 暴露或加密资产。不得因字段名称相似而假定单位或含义相同；例如 VWAP 只有在成交额/成交量的单位、零成交处理、复权口径和时间窗口全部核实后才能生成。

`FactorSpec` 已能表达来源、公式、所需字段、lookback（上限 120）、available_at、横截面/方向、缺失策略和适用范围等，但仓库没有检出 Alpha101、GTJA191、Alpha360、JoinQuant、RiceQuant 等因子目录或对应接入器。Qlib 当前环境可导入 Alpha360；其配置产生 360 项序列特征（open 60、high 60、low 60、close 60、vwap 60、volume 60，最大回看 59）。公式中的close字段引用次数不等于输出列数。Qlib 组件默认 instrument universe 不可直接沿用作本项目 ETF universe，必须显式覆盖。`Alpha360` 可用性不代表它已绑定到此快照或通过了本项目验证。

正式快照、baseline 和既有 run 均为只读证据；外部源授权、数据采购、holdout 读取和新 campaign 均不在本方案执行范围内。

## 3. 来源与因子准入分层

| 来源/因子族 | 当前判断 | 本地实测状态 | 绕开或进入筛选的条件 |
| --- | --- | --- | --- |
| WorldQuant Alpha101 | **优先进入离线筛选的候选来源（限可映射子集）** | 未在本仓库实现/正式测试 | 逐公式锁定论文/目录版本、原始表达式、字段/算子语义；只编译当前数据支持且无歧义的 OHLCV 公式。含 VWAP、行业/市值/外部市场输入的公式先排除，不能用近似字段冒充。Alpha101 论文发布的是研究公式集合，不构成 ETF 上有效或可投资的证据。[论文](https://arxiv.org/abs/1601.00991) |
| Qlib Alpha360 | **条件性离线筛选** | 本机可导入并核对特征配置；未在本快照正式测试 | 显式指定 ETF universe；处理 VWAP 缺失及 volume 单位；测试窗口、NaN、停牌/零成交与复权语义。把 360 序列列作为一个预注册特征组，不要把每个字段都宣称为独立成熟 Alpha。[Qlib loader](https://github.com/microsoft/qlib/blob/main/qlib/contrib/data/loader.py) |
| RDAgent 已完成正式候选 | **实测拒绝** | R01/R03/R05 均 rejected | 具体数值见第 4 节；不进入实盘候选，不把结果归因给外部因子库。 |
| GTJA191 | **暂缓** | 未发现本地目录或实现 | 先取得允许本项目使用的版本化公式/文档，逐项确认字段、窗口、运算符、复权/PIT 和授权；之后只挑可映射子集。DolphinDB 文档可作为公式定义核对来源，不能替代本地实现和数据验收。[官方文档](https://docs.dolphindb.com/zh/modules/gtja191Alpha/191alpha.html) |
| “通联 424” | **暂缓** | 未发现本地目录、接入或授权凭据；具体 424 版本未核实 | 不以供应商公开宣传的“400+”推断与用户所指 424 清单相同。需要确认产品名、版本、license、字段字典、历史 PIT、更新与费用政策；随后做供应商独立对账。 |
| Fama–French | **当前旁路：基准/归因 lane** | 本地未发现对应因子时序输入 | 经典因子是投资组合收益序列，不自然等同于 ETF 横截面股票特征。可在定义匹配的市场/币种/频率数据获得后作风险暴露或比较基准；只有可审计的 ETF 暴露构造才进入信号实验。[官方数据库](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html) |
| MSCI 因子指数/暴露 | **当前旁路：风险和风格参考** | 本地无许可/数据暴露 | 方法文件可帮助定义风格，但专有成分、暴露与历史版本不能由公开方法文本推导。取得许可和 PIT 暴露后再评估是否可作外部特征。[方法文件](https://www.msci.com/indexes/documents/methodology/3_MSCI_Factor_Advanced_Indexes_Methodology_20250203.pdf) |
| JoinQuant / RiceQuant | **暂缓接入** | 未发现本项目配置的客户端、授权或落地数据 | 文档中存在相关因子/API 不代表数据在本机或有再分发/研究许可。须先核验 entitlement、字段历史可用时点、复权口径、速率/费用及结果可重现性，再作为交叉核验或导入来源。[JoinQuant](https://www.joinquant.com/help/api/doc?id=9836&name=JQDatadoc) · [RiceQuant](https://www.ricequant.com/doc/rqfactor/manual/index-rqfactor) |
| 华泰等券商研究报告 | **只作假设来源** | 无机器可读目录或本地公式实现 | 报告中的因子先登记原文页码、样本/标签/成本定义，再由独立实现者转成 FactorSpec；报告绩效不能当成本项目回测结果。[示例报告](https://crm.htsc.com.cn/doc/2019/10750101/9ee613ca-aff7-47a0-ab27-b3f448953585.pdf) |
| 加密资产因子 | **本项目明确绕开** | 无加密数据和资产/交易日历契约 | 24/7 市场、交易所微结构、资产存续和资金费率等与本项目境内 ETF 日历/账本不兼容；需另立项目、数据快照与协议，不混进本次 ETF campaign。[代表性研究](https://arxiv.org/abs/1811.07860) |

## 4. 已完成正式筛选的因子及淘汰原因

下列三项来自 `formal-five-20260924` campaign 的 5 次生成尝试中的 3 个唯一已评估定义；campaign 配额已用尽。每个报告使用五个开发折和三个种子（候选评估表按折/种子汇总 15 cells），不包含 holdout。精确证据位于各 run 的 `first_loop_report.json` 与 `research/paired/runs/<factor>/candidate_report.json`、`ablation_report.json`。

| Run / 因子 | 配对结果 | 风险/压力证据 | 正式结果 |
| --- | --- | --- | --- |
| `tushare-formal-first-loop-20260924-r01` / `vwc_location_20` | 15 cells 中位增量 0.00 pp、均值 +0.615 pp、正增量 7/15 | 12/15 cells 最大回撤高于冻结 12% 上限，最大约 16.98%；换手恶化；压力风险门失败 | **拒绝**：消融未确认、风险超限、压力风险、回撤恶化、非多数折增量、seed 不稳、换手恶化。 |
| `tushare-formal-first-loop-20260925-r03` / `trend_clarity_60` | 中位增量 +0.431 pp、均值 −0.311 pp、正增量 8/15 | 9/15 cells 回撤高于 12%，最大约 17.17%；2x 成本压力的 fold E 两个种子超出 −3% 门槛（−5.32%、−3.99%）；换手恶化 | **拒绝**：绝对风险、压力收益/风险、回撤恶化、seed 不稳、换手恶化。 |
| `tushare-formal-first-loop-20260925-r05` / `up_volume_share_60` | 4/15 cells 正增量，中位 0.00 pp、均值 +0.212 pp；基线/候选中位超额收益约 2.20%/2.32% | 12/15 cells 回撤高于 12%，最大约 17.67%；2x 成本下 1/15 cell 低于 −3%（最低 −5.25%），压力回撤 12/15 超限 | **拒绝**：消融未确认、风险超限、压力收益/风险、回撤恶化、非多数折增量、seed 不稳、换手恶化。 |

Campaign 汇总确认：生成尝试 5/5、唯一因子定义 3、已评估候选 3、接受 0、holdout 未评估、投资接受 0。10 次 provider usage 记录的成本均为 unknown；`measured_cost=0` 只是已知成本小计，不是总费用为零。当前记录覆盖率为 0，usage audit 为 partial。不要以字符数或 tokenizer 估算替代供应商账单。

因此，三项 RDAgent 因子都应留作“有完整拒绝证据的历史样本”，不得在文案、报告或策略中称为 live candidate。是否允许发起下一 campaign 应由用户另行确认，并先明确 provider 费用未知的接受条件。

## 5. 外部因子库的工程接入契约

所有来源先经过离线导入与注册，再允许进入筛选。建议沿用 FactorSpec / FactorRegistry，并补充版本化 SourceManifest，至少包含：

1. **来源身份**：source/provider、产品或论文版本、发布日期、license/entitlement、原文 URI 与内容 hash、导入者和导入时间。
2. **定义身份**：原公式文本、规范化 AST、算子版本、字段名、方向、窗口、横截面分组、定义 hash。别名不同但数学定义相同的公式应聚为同一 identity，变体需记录差异。
3. **数据契约**：字段映射、单位/币种、频率、复权类型、交易日历、universe 与生存状态、上市/退市、停牌/零值、缺失值、available_at 与 PIT 证据。
4. **执行契约**：lookback 限额、合法算子白名单、除零策略、输出数值范围、NaN/Inf 处理、是否 cross-sectional、适用资产范围、确定性运行与输入/输出 hash。
5. **证据链**：snapshot id/hash、factor definition hash、代码版本、protocol id、baseline id、参数/seed、逐折结果、成本/风险/换手/消融、决策原因。来源授权和 provider usage 另行记录；未知必须为 `unknown`，不得填零。

当前 ETF 直接特征与成分穿透应是两条不同 lane：本快照仅支持 ETF 本身的 OHLCV；没有 PIT 成分权重时，不得把个股 Alpha101 公式暗中应用到基金成分。成分穿透需要独立的历史成分快照、复权、权重生效时点和 ETF 持仓变化规则。

## 6. 筛选与验证流程

**S0 来源门**：核实许可、版本、公式文档及 hash；缺任一项则保持 `quarantined`，不计算。对供应商因子同时核验费用和历史数据可用性。

**S1 映射门**：将公式编译到白名单字段/算子；拒绝未知字段、未来可得字段、单位含混、lookback 超限、含隐含成分股依赖或依赖无法证明的横截面算子。VWAP 不得自动用 close 代替。

**S2 确定性/时点门**：同一 snapshot 与 definition hash 重跑一致；执行时间向后平移/未来值扰动不改变历史信号；截断后重算一致；置换 instrument 检查横截面逻辑；核验 available_at、上市/退市、交易/停牌日与缺失行为。覆盖率至少沿用当前协议 95% 门槛，并按 ETF 分类和时期报告，禁止只报总体覆盖。

**S3 低成本预筛**：使用冻结的训练/开发窗口，输出分布、缺失、相关簇、IC/RIC、分层收益、衰减、覆盖、换手和可交易性；所有方向与组合规则只由开发数据确定。多公式批量计算、定义哈希去重和 snapshot 缓存；不能逐因子支付 LLM 费用。多重试验应完整记账，预先定义筛选统计/校正，不能把重复试验当独立证据。

**S4 正式 paired 验收**：对预先登记的小型 shortlist 按当前五折、三 seed、冻结 baseline 做配对比较，提供每折/每 seed、配对 bootstrap/不确定度、增量消融、2x 成本压力、风险/回撤与换手。严格执行已冻结的 −3% 成本压力收益线、12% 最大回撤上限及其余正式 acceptance gates；阈值只能由授权变更流程修改，不因某个因子表现而调整。正式接受只表示“正式研究候选”。

**S5 独立确认与发布**：正式候选才可按授权流程进入独立 holdout；holdout 一次性、盲化并防止研究过程反复选择。随后还需前瞻 OOS、数据/成本/运营审计和独立发布批准。holdout 未通过、未做或证据不全均不得称为投资准备就绪。本轮不读取 holdout。

## 7. 性能、费用和治理优化

- 将每个因子当作纯函数：`factor = f(snapshot, definition, protocol)`；按这三项 hash 缓存结果，缓存必须校验 manifest，源数据或定义变化即失效。
- 先批量本地算子执行与去重，再做轻量预筛；只让极少量通过的定义进入正式评估。Alpha360 按特征族分组消融，避免把 360 列当 360 次独立发现。
- 对 formula family 做相关聚类并保留代表项；记录总尝试数和 selection rule，避免把预筛优化成隐性多重比较。
- 付费 LLM 只可在单独获授权后用于假设生成/代码协助；研究定义的形式化、执行、筛选与拒绝判定都要能离线复现。Provider cost unknown 视为费用审计不完整，而非零成本。
- 每次 run 前确认 campaign attempt cap、物理 dispatch cap、费用上限/未知费用政策三者是不同设置。`max_attempts=5` 只限制 campaign 试验数，不约束所有 provider 调用或货币费用。
- 原始数据、冻结 snapshot、baseline、既有 campaign 与 rejected artifact 均不覆盖；新实现应由新协议/快照或新 run id 产生可追溯结果。

## 8. 实施顺序与验收

1. **P0 因子库注册/隔离**：SourceManifest schema、版本化目录、license/quarantine 状态、定义 hash 与来源审计。离线单测覆盖缺来源、变更 hash、别名重复和未授权来源 fail closed。
2. **P1 字段/算子映射**：先实现 Alpha101 可支持子集；operator parity 用论文公式和独立小样例逐项比对。所有被排除公式记录原因。不得一次导入整套 101 个公式并把未支持字段默认为近似字段。
3. **P2 Alpha360 数据适配**：验证 ETF instrument 列表、vwap 生成/舍弃规则、成交量单位、零成交与复权映射；360 特征逐列与 Qlib loader 对齐；对前视、截断、缺失和重复计算做测试。
4. **P3 低成本筛选执行器**：快照级批算、缓存、去重、候选报告和多重试验记账。测试缓存命中/失效、重启恢复、并行确定性与因子列数/覆盖率守恒。
5. **P4 正式 paired 接口**：只接纳 S0–S3 通过并登记的少数定义，复用既有 frozen baseline 和 formal evaluator；不改 gates。形成逐因子 verdict 与 evidence links。
6. **P5 其他来源适配**：只有在授权、版本化数据和 PIT 语义具备后，再按独立适配器加入 GTJA/供应商/研究报告/风险因子；不同资产 universe 独立协议。

最小验收集：公式单元样例；算子边界/除零；单位与 VWAP 对账；future perturbation 与截断不变性；PIT/universe 过滤；因子确定性与哈希；coverage/NaN/Inf；缓存恢复；source/license fail closed；paired baseline 不变；每折每 seed/ablation/cost/risk/turnover 报告完整；holdout 不可被预筛调用。

## 9. 外部参考

- Kakushadze, *101 Formulaic Alphas*: https://arxiv.org/abs/1601.00991
- Qlib Alpha360 loader: https://github.com/microsoft/qlib/blob/main/qlib/contrib/data/loader.py
- DolphinDB GTJA 191 官方接口说明: https://docs.dolphindb.com/zh/modules/gtja191Alpha/191alpha.html
- Kenneth French Data Library: https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html
- MSCI Factor Index methodology: https://www.msci.com/indexes/documents/methodology/3_MSCI_Factor_Advanced_Indexes_Methodology_20250203.pdf
- JoinQuant API 文档: https://www.joinquant.com/help/api/doc?id=9836&name=JQDatadoc
- RiceQuant 因子文档: https://www.ricequant.com/doc/rqfactor/manual/index-rqfactor
- Tonglian DataYes 产品介绍（仅作供应商产品入口，不据此确认具体 424 清单）: https://www.datayes.com/news/1733.html?lang=en-US
- Huatai 研究报告示例（仅作假设来源）: https://crm.htsc.com.cn/doc/2019/10750101/9ee613ca-aff7-47a0-ab27-b3f448953585.pdf
- Crypto factor research example: https://arxiv.org/abs/1811.07860
