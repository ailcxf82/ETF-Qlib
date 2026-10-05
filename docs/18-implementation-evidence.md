# 文档18实施与验收台账

日期：2026-09-26。状态：实施中；全量单测与路径修复集成回归通过；Alpha101正式campaign v2 已完成（5项正式拒绝、0项工程失败），整体规划验收未完成。

本台账对应 [整体规划](18-integrated-factor-research-optimization-plan.md)，不替代原范围，也不把单测通过当作正式研究接受。历史 run、冻结快照和原协议不改写。

## 最新补记：方案B真实基线及工程终审完成（2026-09-26）

本次修复与方案B研究准备已完成验收；这不代表文档18整体研究规划、W08独立效果验收或投资就绪通过。新基线`tushare-formal-w08-plan-b-baseline-20260926`于12:48 UTC完成，原主PID1328与worker PID20820均退出，exec session26121返回退出码0；未停止、重启或替换运行。

真实结果为五折10个模型运行、10个辅助策略运行、40组基础/2倍成本结果。643项run工件hash、10个模型包及其源码/环境身份核验通过；40组账本对平、开发收益日期索引hash一致且全部早于2026-01-01。源码身份仍为`0671c00f347a6adfc65435a05fb972b5e4683d37f7bbe0349d8e11c3bcbc91cc`。最终`first-loop-readiness`显式绑定该基线，返回`needs_full_validation`、`baseline=reusable`、`blockers=[]`、固定Docker镜像passed；独立确认仍not_run，投资就绪false。

1049项单测/准备集成及1项RDAgent/Docker离线回放通过的原测试证据hash保持不变。35项历史保护源再次核对未变，旧campaign仍5/5耗尽，新campaign未创建，真实provider调用为0。实际运行期间预检曾正确拒绝未完成基线，终态后才允许复用；没有为了验收修改阈值或旧账本。

终审证据：`artifacts/research_audits/plan-b-preparation-20260926-tests/evidence-manifest.json`及`final-readiness.json`。匹配基线的可执行预检命令与五折IC/ICIR表见[文档21](21-research-only-plan-b-runbook.md)。仍未知的复权/事件来源完整性、供应商实时连通及真实独立验证另列为未认证，不以工程完成替代。下方“进行中”记录保留为12:17 UTC的历史观察，不覆盖其阶段工件。

## 阶段补记：方案B预检修复及真实基线进行中（2026-09-26）

详见[文档21](21-research-only-plan-b-runbook.md)。修复`first-loop-readiness`只验证campaign参数却不检查真实耗尽状态的问题；支持显式冻结快照、可复用baseline和Docker检查。方案B不要求重新采集已完整冻结的数据，但检查全文件hash、结构/PIT、配置/股票池身份及研究索引隔离；独立确认缺证据保持not_run，不阻塞研究准备。缺必要数据且没有完整快照仍blocked，不以合成值或平均值补真实结果。

新增baseline请求、worker与复用入口的源码/环境双向身份校验；缺少身份的旧基线不复用。当前源码hash `0671c00f347a6adfc65435a05fb972b5e4683d37f7bbe0349d8e11c3bcbc91cc`。最终全量unit加新合成集成 **1049 passed、6条既有Pandas警告、109.27秒**；额外RDAgent零付费回放集成 **1 passed、67.58秒**，均退出0。新集成真实运行baseline子进程时已将测试用原始目录暂移走，证明冻结快照不仅能通过预检，也能支持该计算路径；只操作pytest临时目录。其余integration未整套重跑。

真实检查：旧campaign 5/5耗尽且缺当前8小时时限，修复后明确blocked；旧-0.03基线因新配置不匹配拒绝复用。现有冻结快照hash检查通过、研究索引1480只ETF；复权与事件来源完整性仍unknown，没有升级为G0通过。Docker Linux和固定digest通过，本地供应商配置存在但未请求联网。预检建议ID `formal-five-w08-plan-b-20260926`未创建，不占任何名额。

已启动真实本地基线 `artifacts/runs/tushare-formal-w08-plan-b-baseline-20260926`，**2026-09-26 12:17 UTC仍running**：主PID1328、worker PID20820，exec session26121；A折两个模型完成，辅助等权组合进入2倍成本压力计算。该运行不调用LLM、不消费真实独立留出值。不可停机、重启或变更当前源码/配置来推进；下一步继续核验该现有句柄，再等待终态和最终复用审核。实际基线完成及目标完成尚未证明，不将本节当作完整验收。

阶段证据索引：`artifacts/research_audits/plan-b-preparation-20260926-tests/verification-progress.json`。35项历史保护源未变，原基线与阈值基线manifest hash分别仍为 `46ec2b9a26b6bc956a3c0af282b19041bbabf1529f2d18709bc13b7ae1f06f18`、`1d4ab485a7721b2b02afbb45f6d0a2f17635c8c28bd9d2e62a22aa708fbf1725`。后续完成时另附终审证据，不覆盖旧阶段工件。

## 最新补记：W08默认参数与暂缓执行（2026-09-26）

按用户授权补齐准备材料，交付[文档20](20-w08-independent-confirmation-defaults.md)、`configs/data/tushare_formal_w08_preparation.yaml`和`configs/research/w08-independent-confirmation-plan.json`。解析后仅acceptance六项相对旧配置改变：累计净收益3%、累计超额1%、回撤12%、年化波动15%、累计执行成本/初始权益2%、252个有效日期。这些是自主工程默认值而非行业平均收益；来源、单位、适用范围与局限逐项写明。旧配置仍保留null与原hash。

真实独立验证暂缓而非通过：新配置仍 `holdout_independent=false`；计划为 `prepared_validation_deferred`，未来候选/起止日期未注册。新材料不能代替v2访问审核或绕过冻结链路。只读核对旧区间日历有164日，不等于有效收益样本，不读价格/收益；R05仍rejected，history unknown，holdout not_run。最新准备工件为 `artifacts/research_audits/plan18-w08-preparation-20260926`，35项保护源hash和5份输出hash核验均未变化/损坏，零provider请求、零holdout claim。

新增11项配置与判定边界用例；针对性73 passed，全量 `tests/unit` 1040 passed、6条既有Pandas警告、108.05秒，退出码0。本轮未改运行时源码，`code_hash()`仍 `b9c7f850a2f82d1989594058037661aaa503c296ec2f4b8d479d50e7aa11f8cc`；未重跑integration或实际研究。证据总索引 `artifacts/research_audits/plan18-w08-defaults-20260926-tests/evidence-manifest.json` 绑定新旧配置、计划、测试与只读准备报告。指标设计技能区分来源事实与暂定门槛；Ponytail技能让本次复用现有配置/判定器，不新增跳过门槛代码。

W08准备子项已交付，真实独立确认和投资准备仍未通过，整体不勾选完成。下面此前“六项参数仍待补”仅描述旧配置，不再作为新准备配置的缺项。

## 最新补记：费用追补延期与未来采集验收（2026-09-26）

按用户批准“历史费用暂不追补，未来改善采集，研究门槛不放松”，新增绑定 `formal-five-20260924` 及原事件链的 `configs/research/formal-five-20260924-accounting-policy.json`。派生报告显示 `deferred_by_user`、`historical_backfill_required=false`，但账务仍partial，10条历史费用和provider usage仍未知；不改旧账本、不释放R02/R04名额，原campaign维持5/5耗尽。本节取代下节将外部费用补齐作为当前必需项的表述，不取代原测试或历史研究结论。

未来调用采用 `rdagent-single-dispatch-usage-v2`，复用现有RDAgent/LiteLLM和GuardedLLM账本。只白名单提取原始供应商响应的请求/响应ID、模型及实际tokens；SDK自动补零与费用估算不算实际值。JSON解析前保存回执，parent核对请求指纹与内容，失败账本仍能保存已验证usage；未知保持null。单次逻辑分派最多一次底层completion，关闭SDK重试、自动续写及隐藏缓存，保留项目自有可信缓存。旧调用网络次数不据此追溯认证。

最终测试命令：`python -m pytest tests/unit tests/integration/test_rdagent_usage_receipts.py -q --junitxml=artifacts/research_audits/plan18-fee-policy-20260926-tests/final-suite.xml`。结果 **1035 passed，7 warnings，97.54秒，退出码0**；包含1029项单测与6项已安装RDAgent/LiteLLM离线集成。覆盖原始响应缺项/部分缺项、实际usage与估算分离、截断续写阻断、解析失败、网络错误、回执缺失/篡改及账本传播。6条Pandas和1条第三方Pydantic弃用警告；无真实provider请求，未重跑其他integration。

当前源码hash `b9c7f850a2f82d1989594058037661aaa503c296ec2f4b8d479d50e7aa11f8cc`；最终JUnit SHA256 `2e49a15946ffa85b8594dff7f9056759a007c83bb92508f49b2a3cd338632f2b`。验收总索引：`artifacts/research_audits/plan18-fee-policy-20260926-tests/evidence-manifest.json`。最新真实只读派生目录：`artifacts/research_audits/plan18-cost-deferral-20260926-r3`，manifest SHA256 `47045954311e7678b1e3351999e73c6daca954fcdd025036575890996eb8295e`；35项保护源全部hash不变，0外部调用、未读真实holdout行情。

W07工程部分按该政策验收，历史账务不声明完整。压力收益-0.03、风险、IC/ICIR方向与逐折门槛、配对/消融/成本、有限campaign及留出门槛均未放松。W08仍缺真实访问历史审核、预先冻结的独立验收六项参数及正式accepted候选。运行身份变化要求后续新protocol与匹配baseline，本次没有启动新研究。实施取舍遵循Ponytail技能：复用既有调用链，不新增供应商客户端。

## W07/W08 补全更新（2026-09-26，前一阶段）

操作交接见 [文档19](19-w07-w08-closure-guide.md)。新增 `research/closure.py` 和 `prepare-research-closure` CLI：按真实 call ID/request hash 生成补填表，核验币种、非负金额、整数 usage、具名审核、来源 hash、逐调用凭证唯一性与已测 usage 冲突；所有对账只生成新派生视图，不能恢复 uncertain 调用、重写旧账本或释放 trial 名额。空白模板仍为 unknown。

W08新增 `holdout-access-audit-v2`，绑定快照及起止日期。`evaluate-holdout --holdout-access-audit`、Python入口、worker和CLI缓存复用均先核验真实审核材料；旧v1只用于兼容只读报告，不满足执行门槛。缺项/旧协议不声明独立/曾暴露/源证据变化均拒绝。不是自动认证人工声明真实性，也不是 accepted 因子或独立验证通过证明。

真实只读交付目录 `artifacts/research_audits/plan18-w07-w08-closure-20260926`：10条费用与provider usage未知，R02/R04失败名额保留，R02独立billing文件缺失；本地holdout usage未见记录，历史是否暴露仍unknown。当前六项独立acceptance配置全null，当前源因子rejected。34项保护源hash核对全部未变，原基线与阈值基线manifest hash未变；0外部调用，未读真实holdout行情值。

新增行为首轮定向106 passed/2 failed（新统计字段被通用secret-key脱敏、旧CLI参数测试未更新），分别通过改用usage字段名和更新必需审核参数契约修复；复验108 passed。再新增双重计费/实测冲突、空模板回读、worker前置审核测试，本次全量unit最终 **1008 passed，6条既有Pandas FutureWarning，92.03秒**。真实Qlib合成留出集成 **2 passed，283.19秒**，覆盖未审核前禁止claim、实际留出模型推理、失败重试、提交中断、缓存复用、重复候选区间拒绝，以及缓存来源被改写时拒绝。未重跑整套integration，也未启动真实研究/holdout。

本次源码hash `583a79f2222636ab528912aba61ee2e9400dd3e36b4b5adab859359e3ae28fc9`。验收manifest为 `artifacts/research_audits/plan18-w07-w08-tests-20260926/evidence-manifest.json`，绑定unit/integration JUnit及真实只读交付manifest；旧A22/r4仍属于旧源码版本，不能代替本次测试。W07/W08工程补充通过，外部费用/访问历史/独立阈值/accepted候选仍未闭合，总体保持partial。

## 已有实现与证据

| 工作包 | 已实施内容 | 已确认的边界 / 尚缺内容 |
| --- | --- | --- |
| W01 | `qualification.py` 根据独立来源生成阶段状态，`first_loop.py` 发布派生资格报告；`audit-research`验证新版qualification路径/hash/状态，旧report缺链接时重算并忽略旧布尔值 | 主审计消费端兼容已覆盖；其他外部/人工消费流程没有纳入程序验证；unknown不是数据通过 |
| W02 | 日级权益与高水位检查、连续超限事件、下一记录交易日/风险检查/触发信息；R05派生审计覆盖15个配对行；Alpha101 #40 的正式配对报告中15/15行日级风险归因完整、无缺失风险检查 | 缺成交证据的最早合法退出时间保持未知；尚未实现或采用新风险策略 |
| W03 | 首批20个Alpha101 ETF适配公式、来源/算子版本、独立数值对照、因果与证券置换测试；确定性执行接入FactorSpec、registry、PIT因子引擎和配对runner；registry深路径Windows读写修复；跨execution模型路径正式修复；v2五个候选均完成正式五折×三seed并拒绝 | v2真实工件的225条模型/子运行引用及文件hash复核通过；路径单测3 passed、Docker配对集成1 passed、全量单测925 passed（v2运行前）。Alpha360组和冻结后推断端到端未完成 |
| W04 | `screen-library`在新run `plan18-alpha101-screen-v2`上真实筛选冻结Tushare快照20项：17项技术通过、3项因覆盖不足不评分；五项shortlist保持与v1一致。新增内层验证期横截面Spearman相关簇报告，阈值规则绑定manifest、纯诊断不淘汰候选；paired正式运行已有信号→选择→目标权重→成交/成本归因 | 观察到稳定五折相关簇：#6/#14正相关、#33/#101负相关，均为至少0.90绝对值；shortlist仍需正式评估，相关不代表可替代或应删除 |
| W05 | 固定1/5/10/20日期限共同成熟样本诊断；从实际回测持仓账本按日期有效的PIT tracking group 归集权重、组内ETF数、现金/应收/分红及权益对账；信号年龄成本诊断；新增`compare-library-horizons`，为冻结shortlist逐期限运行独立formal protocol与匹配baseline | PIT历史分组只读派生审计覆盖5份paired报告、75折/seed行、150个基线/候选侧、18,270个日记录；全部记录完整且权益对账通过，未读holdout。四期限paired执行入口已通过合成恢复/协议身份测试，但当前尚未实际运行，因此仍无四套真实组合净收益、换手、成交成本及压力比较证据；不自动选期限/改变策略 |
| W06 | 复用现有失败卡、提示词和修复上限；实际请求接收测试覆盖量化风险/成本/换手/消融、信号执行缺项、unknown和hash；记忆索引对旧v2卡从已提交结构化反馈只读重建v3投影 | 20个显式登记源中的16条已提交trial已逐字段审计：13条正式评估证据完整进入派生记忆卡，3条技术/质量失败明确按无正式配对评估标为不适用，并保留其失败原因；未登记到source manifest的外部/手工记录不在审计范围 |
| W07 | campaign只读审计与跨campaign谱系；计算缓存绑定实际输入、定义、资格mask、算子/校验代码和环境；批筛checkpoint恢复/hash核验；formal shortlist ledger已提交候选可在外层中断后复用；可信缓存新增manifest和形状/列身份校验；RDAgent transport每逻辑请求最多1次物理dispatch、模糊失败禁止自动重试；新增冻结的每trial dispatch上限、repair配额和campaign墙钟期限；usage audit状态同时核验未结trial、供应商tokens和实际费用 | provider tokens或费用不完整时usage audit保持partial，summary schema升级为v2；供应商真实usage/cost对账仍未闭合；墙钟超限不会强杀已启动的Docker/worker，只在控制点停止后续工作；unknown不得视作0 |
| W08 | 留出资格审计函数与`audit-research --holdout-access-audit`只读接入；经审阅的访问历史和来源hash可补充派生资格，不篡改旧报告；`freeze-model`/`evaluate-holdout`独立执行入口与一次性holdout区间账本已有真实测试夹具集成 | 仍无真实人工访问历史审计、当前配置不声明holdout独立、无正式accepted因子；因此没有实际独立/前瞻通过证据，不能消费真实holdout或声称投资就绪 |

首批公式ID：3、4、6、12、13、14、15、16、18、20、23、26、33、34、35、40、44、45、55、101。它们是本地数学实现和ETF数据适配，不声称供应商原始实现数值一致，也不称为已获利因子。

## 实际只读来源审计

[R05派生审计](../artifacts/research_audits/plan18-r05-evidence-v1/audit_report.json)：

- 数据资格 `unknown`，因子正式结论 `rejected`。
- 留出资格 `ineligible`，留出评估 `not_run`，前瞻 `not_started`，投资准备 `not_ready`。
- 15个配对风险行；未读取留出行情；零外部调用。
- R02/R04为终止run中的未提交尝试，仍保留占用名额；费用未知不改成零。
- 该审计绑定生成时源码身份；后续代码改动不反向更新其hash或冒充最新验收。

## 测试与运行记录

1. 既有阶段记录：`artifacts/research_audits/plan18-tests-v1/unit-results.xml`，902 passed。此记录早于批筛入口，不作为新入口验收证据。
2. 新入口专项：`python -m pytest tests/unit/test_library_screening.py -q`，15 passed（10.12秒）。覆盖真实合成快照、零调用、留出路径隔离、purge、完成后复用、中断后已提交前缀恢复、checkpoint/缓存篡改拒绝、实际输入/校验版本失效、技术失败和PIT分组缺项。
3. 新版本全量单测：`artifacts/research_audits/plan18-tests-v2/unit-results.xml`，917 passed、6 warnings、82.58秒、退出码0。源码hash `43783cab99f07cf29d912aef6ada616bfc49ba1bd475e6b1cecfd49f32d7acaa`；XML SHA256 `8e960a5e62847c81266ba7724caaf4e688d672ca00ca2fe96fdaedc11d37655e`。环境：Python 3.11.15、pandas 2.3.3、NumPy 1.26.4、scikit-learn 1.8.0、pyqlib 0.9.7、RDAgent 0.8.0。
4. 冻结研究快照上的真实零LLM批筛已启动：`artifacts/library_screening/plan18-alpha101-screen-v1`。在结果终止和hash核验前不发布入围数量或性能结论。
5. 后续新增真实请求接收测试：`python -m pytest tests/unit/test_llm_factor_v1.py -q`，24 passed、1.24秒、退出码0。此次只增加测试，不修改上述源码hash；全量917条记录不包含这条新测试。
6. Docker Linux及固定镜像digest就绪。`python -m pytest tests/integration/test_factor_container.py -q --junitxml=artifacts/research_audits/plan18-tests-v2/docker-factor-results.xml`：6 passed、21.54秒、退出码0。覆盖真实容器计算/缓存、未来值污染、NaN和索引错误拒绝。
7. 同输入Alpha101 #6计算缓存实测：首次计算/校验/发布18.9524秒；hash验证后的缓存读取0.1753秒，manifest及结果hash一致。该计时范围为函数内部缓存路径，不包含读取快照、构造缓存键、模型拟合或正式回测，不能据此推断整体研究加速倍数。
8. RDAgent真实适配器/CLI/两轮恢复/注册库/反馈的零付费回放集成：`python -m pytest tests/integration/test_rdagent_research.py -q --junitxml=artifacts/research_audits/plan18-tests-v2/rdagent-replay-results.xml`，1 passed、48.02秒、退出码0。此为合成研究回放，不是新的正式campaign或真实市场接受证据。
9. 修复后专项回归：`tests/unit/test_library_screening.py tests/unit/test_factor_registry.py tests/unit/test_factor_identity.py tests/unit/test_research_campaign.py tests/unit/test_llm_factor_v1.py`，66 passed、69.33秒。覆盖真实请求接收、注册、formal入口、campaign及恢复边界。
10. 当前全量单测：`artifacts/research_audits/plan18-tests-v3/unit-results.xml`，922 passed、6 warnings、294.31秒、退出码0；XML SHA256 `9fa8e98d72820db9879c253318081bc8325a9a94ab94768bcaaf63a47a40c240`。测试对应源码身份 `087cb3d265553afc66b731ce05f5e0c883d461e24863db6a20b3d9b2e21f696b`。
11. 当前核心集成：`tests/integration/test_factor_container.py tests/integration/test_rdagent_research.py tests/integration/test_paired_research.py`，8 passed、16 warnings、185.86秒、退出码0；JUnit XML `artifacts/research_audits/plan18-tests-v3/core-integration-results.xml` SHA256 `b793050e6d9a64c3789d03e2c27a30a5a18497230b094e852018b28717822f07`。覆盖Docker隔离、RDAgent零付费回放以及真实配对基线/候选、压力、消融和验证复用；formal shortlist驱动测试自身用合成decision fixture，不宣称真实shortlist正式结果。
12. 实际筛选产物复核：`artifacts/library_screening/plan18-alpha101-screen-v1` manifest文件hash全部通过；20个定义、5个shortlist（#40、#44、#14、#3、#55），零外部调用、零正式评价、未读holdout。筛选报告SHA256 `290cbf25bfed2b67ccfa085e1c4e3ded0a384323e2c567874939b64752e172f4`；shortlist SHA256 `f94da9976cedf57a977eca7cb4b7f8308eed96c7adb39f92f8c874ecf36a304e`。该批为一次单内层切分的Ridge proxy；选择偏差和小样本限制仍在，不等于候选有效。
13. `git diff --check`通过；仅有仓库既存LF/CRLF提示。以上运行均不连接券商，未发起provider调用；费用为不适用/未分派，不得记成供应商实测成本为零。
14. 在上述回归通过后，启动真实shortlist正式评估 `artifacts/library_evaluations/plan18-alpha101-formal-20260925-v1`。冻结快照完整hash校验后，#40通过可信计算；其正式配对结果为 `rejected`，判定原因含消融未确认、绝对回撤上限、压力收益/风险、回撤恶化、折间增益不足、seed稳定性和换手恶化。15个折/seed配对的超额收益增量中位数为0（5正、3负、7零），候选最大回撤17.51%，2倍成本压力最差超额收益增量-8.78%，换手增量范围约-2.54至+8.07；日级风险归因15/15完整且无缺失检查。因此不是单一门槛边缘失败。#44和#14都完成15/15候选模型训练和消融复用，但终审前失败、均没有`evaluation.json`；二者`worker_error.json`均报`ConfigurationError: Output must be a child of the allowed artifact root`。根因由#44路径逐项复核：缓存baseline模型路径引用旧execution，候选模型路径引用当前execution；`paired._verify_references`把所有model path限制在当前worker的`config.artifact_root/models`，使缓存复用baseline越界。它是工程失败，不是正式因子拒绝，也不能算accepted/inconclusive。campaign主进程PID 6344仍存活，#3（trial_index 3/5）基线三seed复用完成，候选seed 42五折完成、seed 43 A折运行中；不停止或重启，待当前run终态后修复并测试此路径信任边界。此campaign无provider dispatch、不读holdout。
15. W01状态契约专项复测：`E:\tools\anaconda\envs\qlib_zhengshi\python.exe -m pytest tests/unit/test_research_qualification.py tests/unit/test_first_loop_readiness.py -q`，20 passed、0.47秒、退出码0；当前`src/etf_ml`代码hash与全量单测记录一致，为`087cb3d265553afc66b731ce05f5e0c883d461e24863db6a20b3d9b2e21f696b`。这覆盖资格状态函数和readiness门控，不代表所有历史报告消费端均已兼容核验。
16. #44路径边界只读复核：使用真实`baseline_report.json`和`candidate_report.json`逐条调用`ensure_within`；15/15缓存baseline模型路径在当前execution模型root外，15/15候选模型路径在当前root内；将共同受限根提升到`research/executions`后两组共30/30路径都通过。该证据确认失败来自交叉execution缓存引用与过窄的局部root校验；尚未修改实现，也尚无回归测试证明修复。
17. 路径修复隔离探针：将`paired.py`复制到临时目录，只在临时副本中允许经过`research/executions/<id>/artifacts/models/<model_id>`布局约束的跨execution模型引用；用#44真实baseline/candidate/ablation报告运行 `_verify_references`，30条模型引用、子运行manifest与文件hash全部通过；将一个candidate model path改到信任根外后断言被`ConfigurationError`拒绝；进程退出码0。工作树源码未改。此为实际工件上的定点修复验证，不替代正式源码回归、Docker集成或campaign终态验收。
18. 2026-09-25 14:12 UTC campaign活性复核：主进程PID 6344、worker PID 26876仍存在且Responding；两者CPU累计值继续增长。#3 seed 43于14:11:45 UTC完成五折，seed 44于14:12:14 UTC启动A折；campaign `status.json`仍为`running`，未停止、未重启。此记录更新此前seed 43仅完成A折的旧进度描述；源码仍未修改，冻结快照与baseline未变。
19. 路径越界根因回归测试已新增于`tests/unit/test_paired_references.py`。运行`E:\tools\anaconda\envs\qlib_zhengshi\python.exe -m pytest tests/unit/test_paired_references.py -q`：1 passed、1 failed（退出码1）；失败项准确复现同一`research/executions`内旧baseline模型被限制在当前execution `models`目录而拒绝，越界模型仍按预期拒绝。此为修复前红测，生产源码未改；campaign运行中，不将红测记录为验收通过。
20. 2026-09-25 14:15 UTC：#3完成候选seed 42/43/44全部五折及消融复用，随后在`_verify_references`终审阶段失败；execution `0cf1ff983b54cfd6c9bd9142`的`worker_error.json`确认`ConfigurationError: Output must be a child of the allowed artifact root`，未生成正式`evaluation.json`，因此记为工程失败而非因子拒绝。campaign主进程PID 6344仍存活并已启动最后一项#55（trial 5/5）；不重启、不改生产源码。
21. 2026-09-25 14:28 UTC：#55已进入paired候选训练；seed 42及43五折完成，seed 44开始。campaign主进程PID 6344与worker PID 27640存在，进度日志更新至seed 44；run仍为`running`，继续保护当前源码身份。
22. 2026-09-25 14:31 UTC campaign终态审计：主进程PID 6344及worker已退出；`status.json`和`formal_evaluation_report.json`均为`completed`，尝试5/5，0外部调用、holdout未读取、投资准备`not_ready`，费用`not_applicable_no_provider_dispatch`。#40为唯一正式已评估定义且`rejected`；#44/#14/#3/#55均在model_evaluation阶段以相同`ConfigurationError`失败，未生成正式`evaluation.json`，不是经济拒绝。冻结snapshot id与protocol id与campaign manifest一致，原baseline目录与snapshot目录仍在。
23. #40正式证据更精确汇总：15个配对行（5折×3seed），增量正/负/零=5/3/7，中位数0；候选最大回撤17.509%，2倍成本下候选最差超额收益-5.246%（比例值）、相对基线最差增量-1.482%，2倍成本最大回撤17.796%；换手增量区间-2.537至+8.069（turnover定义单位）；group ablation `rejected`；日级风险归因15/15 `completed`。拒绝原因：`ablation_not_confirmed`、`absolute_risk_limit`、`cost_pressure_return_limit`、`cost_pressure_risk_limit`、`drawdown_deterioration`、`no_majority_fold_increment`、`seed_instability`、`turnover_deterioration`。本条纠正并取代第14条的“最差增量-8.78%”描述；应按当前原始evaluation和baseline/candidate cost-stress工件中的-1.482%增量、-5.246%候选绝对值引用。
24. 终审跨execution路径修复：`paired._verify_references`先接受当前model root的直接子模型，否则仅接受`research/executions/<execution_id>/artifacts/models/<model_id>`精确布局，并继续校验child-run manifest/文件、model manifest、model id及bundle hash。新增`tests/unit/test_paired_references.py`覆盖合法旧execution缓存、artifact根外拒绝和execution根内错误布局拒绝；修复前红测1通过/1失败，修复后3通过。修复后对本次5个真实paired run重新执行验证：5/5 run通过，225个报告行引用的child-run工件及model bundles均完成hash核验。
25. 修复后的集成/全量测试：`tests/integration/test_paired_research.py` 1 passed、16 warnings、95.22秒，JUnit SHA256 `48C0D403B9943199A35B4E6B18AD8946AEE1A5A57ABA27D8D828A5C713B04FCE`；`tests/unit` 925 passed、6 warnings、444.84秒，退出码0，JUnit SHA256 `732AD9D5C4348EDCAA94E2ED8D86216A80C1F7833B2A8E6096EA5EE9C75AA9DB`。测试时`code_hash()`为`862f4e72198c15a424688f0b2743261d2b9d68a28c445f47d02124d71f43439d`。全量`git diff --check`退出码0；输出仅有工作树既存LF/CRLF转换提示。
26. W07跨campaign只读谱系已实现于`research.audit.campaign_lineage`，报告绑定每个`campaign.json` SHA256、event-chain head、5次上限、proposal/已提交候选、定义重复及未结attempt；既有ledger不重写、不自动阻拦不同protocol的重试。专项`tests/unit/test_research_audit.py` 4 passed，含同一定义在不同protocol/两个campaign重复以及一个open proposal的归属和无写入核验。真实只读扫描当前`research_campaigns`得到2个campaign、总尝试10/10、跨campaign重复定义0，退出码0。
27. 跨campaign谱系最终源码回归：全量`tests/unit` 926 passed、6 warnings、389.73秒、退出码0；JUnit `artifacts/research_audits/plan18-tests-v6/unit-results.xml` SHA256 `B94C547291724DFBF652E6E4C62DCA2CC522CA87C76CB7D6CA2FFDCDDF3F25B4`。RDAgent replay集成`tests/integration/test_rdagent_research.py` 1 passed、74.42秒、退出码0；JUnit SHA256 `EA41A4B8E6E1897D12B1CEF8B860199571B0996C4A7050E1CC4E6F8866589B4E`。该源码身份`code_hash()`=`facca8c2ccbf0094056b3d105eb39aa3133e52ca0e56ae40e06b7298ed3341b6`；全量`git diff --check`退出码0，只有既存LF/CRLF提示。

可恢复入口：

```powershell
& E:\tools\anaconda\envs\qlib_zhengshi\python.exe -m etf_ml.cli screen-library --config configs/data/tushare_formal_first_loop.yaml --snapshot artifacts/formal_tushare/data/8b384b5d9f8fa10d3d7b6aebbf11fd9463dc76cc9fe203d38dfc6f5ad4322831 --run-id plan18-alpha101-screen-v1
```

只有进程确认终止后才能恢复；活进程不重启。相同ID要求配置、代码和环境相同；身份变化必须使用新ID并明确继承选择历史，不能覆盖旧结果。

## 原验收矩阵的未闭合项

| 原编号 | 当前证据状态 |
| --- | --- |
| A01–A02 | qualification stages、first-loop发布与`audit_research`兼容消费已测试；真实R05审计见`plan18-w01-status-v1`，旧报告以`legacy_recomputed`读取且不使用固定false布尔值，阶段结果按独立证据为data unknown/factor rejected/holdout ineligible |
| A03–A05 | 新回测逐日风险卖单意图/不可卖阻挡与实际交易/账本关联已测试；真实旧R05风险对照15/15均partial，v2旧工件订单关联未知；同开盘决策执行时序及反事实最早合法退出仍未证明，未改变策略 |
| A06–A08 | 20公式数值/因果/置换及VWAP/Alpha360单测已有；冻结真实快照完成20项ETF适配预筛，不构成正式接受 |
| A09 | 冻结快照零LLM实际预筛完成；控制链合成集成通过；v2真实shortlist正式campaign终态：#40/#44/#14/#3/#55均有正式`evaluation.json`且拒绝，0项工程失败；5×15组真实报告引用hash核验通过 |
| A10–A12 | 技术准入、内层切分、缓存恢复及候选相关簇已有专项测试；新冻结快照screen-v2已产出680条折/因子对诊断行及两组五折稳定簇；paired正式报告含信号到执行诊断；相关只用于冗余解释，不自动剔除 |
| A13–A14 | 固定1/5/10/20日共同样本信号衰减、新回测按PIT tracking group计算持仓权重已实现；真实v2 150个回测侧、18,270日全部权益对账通过。四个标签期限真实campaign均终态，20个候选全正式rejected、工程失败0；多个候选触发12%回撤或2倍成本-3%压力线。PIT暴露/缺历史分类按既有证据通过；不得按结果择优期限或改写旧protocol。|
| A15 | W06下一假设/卡片/未知边界覆盖测试通过；真实只读报告 `plan18-feedback-coverage-v6` 覆盖16条已提交trial，历史source card的已知字段从结构化反馈重建为v3提示投影；13项正式评估传递成本/消融/风险/换手/执行/hash/unknown，3项无正式评价的失败保留技术失败原因且评价字段为not_applicable |
| A16–A17 | 5次formal trial预算、campaign账本恢复、并发进程争抢上限5（8进程恰有5成功/3拒绝）、每trial 5次dispatch/2次repair及墙钟控制点有测试；RDAgent每请求1次物理dispatch、预先意图记录、模糊结果不重发、失败attempt进入usage摘要、跨阶段限额不可变均已测试；真实供应商对账仍未闭合 |
| A18 | unknown、零LLM和unlimited记录规则已有单元/回放测试；campaign summary v2会将unknown费用、缺失供应商tokens和未结trial准确标为partial；真实供应商逐项usage/cost凭证和成本仍为unknown，不能标为实测完成 |
| A19 | 计算缓存绑定数据/定义/算子/环境并验证hash；评价协议身份绑定snapshot、baseline、universe、label、model、seed、source与environment，身份变化及runtime漂移 fail-closed 有测试；Docker/Qlib同身份paired评价复用、子工件hash拒绝通过；单次干净首跑/缓存复用同条件测得12.590/0.409秒，JUnit证据留档，不外推通用加速 |
| A20 | Alpha101多期限campaign四个期限各5项均完成正式五折三seed、消融/风险/成本终审并正式拒绝，工程失败0；各期限paired及引用hash核验通过，没有accepted。不能把shortlist、IC/ICIR折级通过或哈希验证当作accepted |
| A21 | `audit-research`可选核验hash绑定的人工访问审计并与原发布资格分开验证；`freeze-model`、`evaluate-holdout`及一次性区间claim经合成快照集成测试；真实访问历史/accepted候选缺失，所以仍无holdout通过结论 |
| A22 | 阶段验收包已创建并校验，绑定当前源码/配置/snapshot/baseline/审计/单测XML与Docker配对计时XML；记录dirty worktree和所有未闭合项。包为partial而非整体完成，SHA256 `71c669fe33f8cd22b840a492e50742f1007dcd6cbc9b90dfb2344521b2c726e9`，路径见`artifacts/research_audits/plan18-a22-evidence-20260926/evidence-manifest.json` |

整体目标保持未完成：W07尚需人工/供应商提供真实usage与cost凭证并解决未结usage slots；W08尚需真实holdout访问历史、合格accepted候选和未消费的独立窗口。计划内确定性库因子正式评价和多期限campaign已执行，但目前没有合格因子。不得通过放松门槛或把shortlist改名为accepted结束任务。

## v2 Alpha101 正式campaign终态补记（2026-09-26）

Run：`artifacts/library_evaluations/plan18-alpha101-formal-20260925-v2`；campaign：`plan18-alpha101-formal-20260925-v2`。正式协议 `8e8b2926485e1c7978f41d5eedd65c4a16f4f871f94efcd9a4bd6d505d196afc`，冻结快照 `8b384b5d9f8fa10d3d7b6aebbf11fd9463dc76cc9fe203d38dfc6f5ad4322831`。进程已终止，status 为 completed，尝试5/5、提交5、open 0、事件15/15；事件链头 `67515705c5ebb39d55424b441e735a752eb7bd5af4f713d7db71514a59278d9b`。只读 `CampaignLedger.audit_view()` 成功。

五个候选（#40、#44、#14、#3、#55）全部有正式评价文件并被判为 rejected；没有工程失败。逐个调用生产 `_verify_references` 后，5/5 paired run 通过；候选/基线/消融报告共225条折/seed行引用的子运行与模型bundle哈希均完整。五份`evaluation.json` SHA256 与正式汇总中的evaluation ID一致。每项评价15行（5折×3seed），最大回撤约17.10%–18.13%，均高于12%绝对风险线；每项压力倍率2.0下最差候选超额收益均为负（约-1.48%至-5.25%），风险/增量/消融/稳定性/换手的失败理由并存。配对超额收益增量中位数五项全为0；正/负/零行数分别为：#40 5/3/7、#44 0/2/13、#14 4/2/9、#3 1/2/12、#55 2/2/11。该模式表明当前主要瓶颈是策略/基线的绝对风险和因子边际贡献普遍不足，而不是单纯的模型训练或路径工程故障；不能据此挑出“最好者”绕过门槛。

本次零provider dispatch，费用状态为`not_applicable_no_provider_dispatch`；ledger中的已知费用小计0只表示没有发生供应商调用，不是供应商价格实测为零。`unknown_cost_calls=0`。Holdout未读取，投资准备`not_ready`。这属于开发集正式否决结果，不构成独立留出确认、前瞻OOS或投资建议。

本补记为工件审计，不修改任何历史campaign、快照、baseline或协议。下阶段仍需处理Alpha360与冻结推断、信号期限的成本后效用/历史分组持仓拥挤、完整反馈覆盖、物理dispatch与usage幂等政策、冻结包及独立确认入口；v2并未关闭整体规划。

## 2026-09-26 W04 相关簇诊断实现

`src/etf_ml/research/library.py` 新增 `factor_correlation_diagnostics`，只读取筛选已验证因子的缓存结果，并仅在预先冻结的内层验证mask中逐日期计算横截面Spearman相关。筛选规则固定绝对中位相关阈值0.90、严格多数折一致才连边；报告同时保留折级可用日期/相关、簇和未聚类定义，规则效果明确标为`diagnostic_only_no_candidate_exclusion`。`screen_library` 输出独立`correlation_diagnostics.json`，报告绑定其SHA256；checkpoint恢复后仍从已验证缓存重建，不改已完成旧run。

验证：`E:\tools\anaconda\envs\qlib_zhengshi\python.exe -m pytest tests/unit/test_library_screening.py -q`，21 passed（12.87秒）。覆盖真实合成快照筛选产物/哈希复用、同向及反向冗余簇、样本交集不足时的unknown/unavailable；首次测试发现MultiIndex在构造选样索引时退化为普通Index，改为保留布尔选择后的原MultiIndex后全过。此改动后尚未运行全量tests/unit或集成套件；v2正式campaign身份绑定旧源码，不会使用新源码冒充旧run或自动重跑付费研究。

## 2026-09-26 W05 信号年龄及成本分组

`signal_to_execution_diagnostics`现从已配对的执行交易日历计算每个决策的`signal_date→decision date`交易时段差；基线与候选日历不一致或日期无法定位时报告缺项/partial。执行成本按候选信号年龄分组保留baseline、candidate、delta，不据此推断成本或收益的因果关系。旧报告字段不完整时仍显式partial，不重跑其回测。

只读派生审计：[plan18-w05-signal-age-v1](../artifacts/research_audits/plan18-w05-signal-age-v1/diagnostics.json)，输入是v2五因子的baseline/candidate报告hash，未修改这些报告、未读取holdout。5因子×15折seed=75行，900/900匹配决策日有同一执行日历且信号年龄可计算；全部为1个交易时段。该年龄下每因子共180次配对决策，候选减基线的执行成本合计分别为：#55 -181.07、#44 -68.24、#3 +229.11、#40 +1699.39、#14 +137.26（回测账本货币单位）。这些成本差仅解释成交成本观察，策略净收益仍需按正式组合报告判断，不能跨候选加总或据此放宽正式门槛。

## 2026-09-26 W05 PIT持仓暴露与单测复核

`portfolio_exposure_diagnostics`读取已保存的实际`positions.parquet`和`ledger.parquet`，按每个持仓日期对应的PIT `tracking_group`归组；不使用当前分类回填历史。报告逐日给出单ETF/组权重、同组多ETF数、现金/应收/分红暴露与独立权益对账。历史PIT分组未被资格清单确认、字段缺失或账户记录不完整时分别返回`bypassed`/`partial`，而非推算补值；账本数量或权益不平则质量拒绝。配对诊断把基线和候选的暴露分开报告。固定1/5/10/20日诊断使用同一“标签已成熟”的样本，不读取未来尚未成熟标签。

只读审计：[plan18-w05-pit-exposure-v1](../artifacts/research_audits/plan18-w05-pit-exposure-v1/portfolio_exposure_report.json)，绑定冻结快照`8b384b5d9f8fa10d3d7b6aebbf11fd9463dc76cc9fe203d38dfc6f5ad4322831`和源码hash `f698510ad4e82116a597b7381aecc009a505af8a0d1aab0f3e581a4c65f000b6`。覆盖5份paired报告、75折/seed配对、150个基线/候选侧，共18,270条日记录，PIT tracking groups已核验；全部日记录权益对账完成。holdout边界为`2026-01-01`，`holdout_values_read=false`；复核期间192个输入hash未变化。此为持仓暴露的回测账本审计，不等于独立样本或投资确认。

新增回归`tests/unit/test_v2_diagnostics.py::test_portfolio_exposure_uses_date_valid_pit_groups_and_reconciles_equity`，覆盖日期变化的PIT分组、组内重复持仓、缺组partial、未经核验的PIT绕开及权益对账错误拒绝。集成测试`tests/integration/test_paired_research.py`断言真实Docker/Qlib配对回测的基线/候选持仓暴露完成且逐日对账。当前完整单测：`E:\tools\anaconda\envs\qlib_zhengshi\python.exe -m pytest tests/unit`，945 passed、6 warnings、96.46秒、退出码0。警告为pandas未来行为提示，不是失败。`git diff --check`退出码0，仅有工作树LF/CRLF转换提示。

阶段性记录（四期限campaign前）：当时尚未做四种持有期/标签下可比组合构建、成交与成本压力回测。该状态已由下文“2026-09-26 W05多期限Alpha101 campaign终态补记”更新：四期限均有独立协议和匹配baseline，20项正式rejected、工程失败0；旧screen与paired结论未回写。

## 2026-09-26 W07 usage完整性与W08留出审计接入

W07修复`CampaignLedger._summary_locked`：旧`usage_audit_status`只看open trial，存在“已结束trial但provider费用或token明细unknown仍显示completed”的状态漏报。现在`research-campaign-summary-v2`同时检查未结trial usage slot、未知实际费用、缺失供应商输入/输出token，输出`usage_audit_reasons`及`partial/completed`；无provider calls且trial已闭合时仍可completed，不伪造成本值。新增测试分别覆盖未知费用、未知tokens和全量已知三种情形。该逻辑确保unknown显式暴露，但没有供应商凭证就仍不能关闭真实usage/cost对账。

W08为只读`audit-research`增加可选`--holdout-access-audit`。它接受人工完成的`holdout-access-audit-v1`，要求holdout独立性已在protocol声明、protocol/访问审计/快照三者的日期边界一致、历史完整、无历史择优/结果访问、具名审核人/时间和非空源凭证；所有源文件hash在审计前后复核。边界缺失则unknown，互相矛盾则ineligible。原first-loop发布资格仍按原输入重算核验，再把人工审计作为补充证据生成派生资格，旧run和访问审计均不改写。`freeze-model`、`evaluate-holdout`及`HoldoutUsageStore`的一次性区间、身份隔离和不可重启已由合成快照集成覆盖；这一测试证明软件通路，不证明当前真实holdout具备独立性。

验证：W07/W08相关回归`tests/unit/test_research_audit.py tests/unit/test_research_qualification.py tests/unit/test_research_campaign.py -q`曾30 passed；加入快照边界fail-closed保护后，`tests/unit/test_research_audit.py tests/unit/test_research_qualification.py -q` 15 passed。`tests/integration/test_holdout_pipeline.py -q`，2 passed、283.18秒；`tests/integration/test_finalize_cli.py -q`，1 passed、72.42秒。均使用临时合成工程/快照，无provider dispatch、无真实holdout读取。最终全量`tests/unit`：949 passed、6 warnings、88.15秒、退出码0；JUnit `artifacts/research_audits/plan18-tests-v20/unit-results.xml` SHA256 `d8fae3349a70b526ac422e1d445f58fe336a1840c7f2ef8494b6fcfca9b25dde`，测试源码身份`code_hash()`=`5d04b61a41b1da4c7c05f3ebabf30fa95dc7892d02ecf45a9788d9cc3926bf27`。全量集成套件仍未重跑；`git diff --check`退出码0，无空白错误（仅Git换行提示）。W07仍需真实provider receipts；W08仍需人类审阅的真实访问来源与有效candidate，否则资格保持unknown/ineligible。

## 2026-09-26 W05 多期限成本后组合评估入口

**运行状态更正（2026-09-26）**：本节较早记录的“尚未执行命令”已过期，以下新增真实运行证据取代该状态描述。代码、协议与冻结输入未因本次运行而修改。

**滚动进度更新（#3终态）**：Alpha101 #3 的h=1正式配对已完成，状态`rejected`，15个fold/seed行；原因：`ablation_not_confirmed`、`absolute_risk_limit`、`cost_pressure_return_limit`、`cost_pressure_risk_limit`、`drawdown_deterioration`、`no_majority_fold_increment`、`seed_instability`、`turnover_deterioration`。15行中9行候选回撤超过12%、9行2倍成本回撤超过12%、5行2倍成本超额收益低于-3%；7/15行回撤恶化、6/15行换手恶化，`volume_price`组消融为rejected。evaluation SHA256=`faf88a1590b603139060e22d3a3a3f5973e9da026913d75a12e55e096a74d72d`，paired report SHA256=`08f5520fec1eb15b746e204331e74354567748a394118e63eae4cbe4007e82b3`，run manifest SHA256=`446c323edbf4e62ec250b9612eb89aad5563794ae09a5b95672982bafa163b33`。外层已启动h=1第5/5候选Alpha101 #55；其余h=1及h=5/10/20终态证据仍缺。

**滚动进度更新（#44终态）**：Alpha101 #44 的h=1正式配对已完成，状态`rejected`，15个fold/seed行；8项拒绝原因与#40相同。15行中10行超过12%候选回撤上限、10行超过2倍成本回撤上限、5行2倍成本超额收益低于-3%；2/15行回撤恶化、2/15行换手恶化，`volume_price`组消融为rejected。evaluation SHA256=`53d53ba345933c8b335f4934f43c7392ec61aa010be69101085b75536fc1ce24`，paired report SHA256=`d1db05b1feee04a7451f2bf69411b0f4e508ee793ec372dc1402a1a011c10f97`，run manifest SHA256=`c7aaa1abf32c7c0b9020a840c418e509d8f31d06c4b9c79c79e60bf31dcfdbd9`。外层h=1第3/5候选Alpha101 #14已启动；h=1余下候选及h=5/10/20仍没有终态证据。该结果仅为冻结开发协议内正式拒绝，不构成跨期限比较、holdout/OOS或投资结论。

**滚动进度更新（#14终态）**：Alpha101 #14 的h=1正式配对已完成，状态`rejected`，15个fold/seed行；原因：`absolute_risk_limit`、`cost_pressure_return_limit`、`cost_pressure_risk_limit`、`drawdown_deterioration`、`seed_instability`、`turnover_deterioration`。15行中10行候选回撤超过12%、10行2倍成本回撤超过12%、4行2倍成本超额收益低于-3%；3/15行回撤恶化、4/15行换手恶化，`reversal`组消融为rejected。evaluation SHA256=`865421d008ddab2c95f77c6f70004544cfaaedde1a27fba1605a332e184947d2`，paired report SHA256=`32ce05394719b31d21c5a4e7522d8977e80a89a79f5a3f5e98fc882d1a213d2c`，run manifest SHA256=`2293b886e67c085f2b056926b8d0b696c33ded3c2ee3dbdf8efaeb87981d3711`。外层已启动h=1第4/5候选Alpha101 #3；其余h=1候选和h=5/10/20仍无终态证据。

外层run `plan18-alpha101-horizons-v1` 使用冻结snapshot `8b384b5d9f8fa10d3d7b6aebbf11fd9463dc76cc9fe203d38dfc6f5ad4322831`、筛选manifest SHA256 `210548c9153acaa67fc17d6580c5e43ef58d242fd47a5a99aef6169d56187138`、冻结源码身份`eac2c70a168241ae2928902bfd3d0e7faacc22a70fcabe7a6a96b2055f6b8c75`。h=1 campaign manifest明确`external_calls_allowed=false`、`holdout_read=false`；截至本记录时外层仍运行。

h=1第1/5候选Alpha101 #40已完成正式配对，15个fold/seed行，状态`rejected`，原因：`ablation_not_confirmed`、`absolute_risk_limit`、`cost_pressure_return_limit`、`cost_pressure_risk_limit`、`drawdown_deterioration`、`no_majority_fold_increment`、`seed_instability`、`turnover_deterioration`。量化复核：15行中9行候选回撤超过12%绝对上限、9行2倍成本回撤超过12%、4行2倍成本超额收益低于-3%压力线；仅B/E两折的种子中位超额收益增量严格为正（2/5折），7/15行换手恶化、3/15行回撤恶化；volatility组消融为rejected。此结果是开发集正式拒绝，不是因子库初筛拒绝，也不是holdout/OOS或投资结论。evaluation SHA256=`4501216ae6efc06245b8ce174896f1c6ff063f64e473121814a06c48ad64e901`，paired report SHA256=`5efd89fb2bc80ce5867ae8041ef11778c649ee8afa4211fff130297de8987387`，run manifest将这些文件hash绑定。外层随后已开始h=1第2/5候选Alpha101 #44，运行进程仍活动。其余h=1候选及h=5/10/20仍无终态结果；不能据#40或单一期限择优。

新增`compare_library_horizons(config, snapshot, screening_run, run_id)`与CLI命令`compare-library-horizons`。输入限定为已完成、hash验证的冻结shortlist（1–5项）；对每个公式固定运行标签期限1/5/10/20日，分别调用现有`evaluate_library_shortlist`，因此每个期限都建立新的protocol identity与匹配baseline，正式执行原有五折×多seed、消融、风险、成本压力与换手评价。标签成熟与purge仍由`available_time`/`learning_mask`共同执行；`ComparisonProtocol.time_block_length`至少20。资源上限固定为4个campaign、每个campaign至多5个候选，即最多20个候选-期限正式配对；不发provider调用，不访问holdout。

比较总报告校验四个protocol互异、各子报告identity/hash匹配、同一shortlist完整、零外部调用及holdout未读。它明确`horizon_selection=none_development_results_are_not_independent_confirmation`，不合并、排序或挑出一个最好期限；任一期限的候选accepted仍须理解为该开发protocol内结论，不能通过跨期限择优绕过确认门槛。汇总RunStore复用时重新核对子报告哈希。CLI示例：

```powershell
E:\tools\anaconda\envs\qlib_zhengshi\python.exe -m etf_ml.cli compare-library-horizons `
  --config configs/data/tushare_formal_first_loop.yaml `
  --snapshot artifacts/formal_tushare/data/8b384b5d9f8fa10d3d7b6aebbf11fd9463dc76cc9fe203d38dfc6f5ad4322831 `
  --screening-run artifacts/library_screening/plan18-alpha101-screen-v2 `
  --run-id plan18-alpha101-horizons-v1
```

本轮只跑入口测试；**未执行上述命令**，因此未创建新formal horizon campaign、未训练模型、未读取holdout。W05仍需在获准运行窗口下得到真实四期限paired结果，且期限变更不得称作独立确认。定点`tests/unit/test_library_screening.py tests/unit/test_research_campaign.py -q`：37 passed；最终全量`tests/unit`：950 passed、6 warnings、87.11秒、退出码0，JUnit `artifacts/research_audits/plan18-tests-v23/unit-results.xml` SHA256 `2ca479a5eec329ee007dcbea7a328b2115637ed07a1b52308c52a580432af1b2`。最终源码下实际paired集成`tests/integration/test_paired_research.py -q`：1 passed、16 warnings、24.38秒，JUnit `artifacts/research_audits/plan18-tests-v23/paired-results.xml` SHA256 `ca6c6431a6db751e6562af31bb746a09f5dd510f3aed0dac9b892642ac6533a8`。源码身份`code_hash()`=`eac2c70a168241ae2928902bfd3d0e7faacc22a70fcabe7a6a96b2055f6b8c75`；全量集成套件未运行，`git diff --check`退出码0且无空白错误。真实四期限结果尚缺。

W05增量专项：`tests/unit/test_v2_diagnostics.py tests/unit/test_development_feedback.py tests/unit/test_library_screening.py`，47 passed（18.14秒）。注意随后源代码在本记录后仍有变更，最终全量回归和当前源码hash见更新的测试记录；本专项结果是那次运行时的直接证据。

后续补充：开发集预测衰减诊断固定为1/5/10/20交易日，在四个期限的共同成熟样本上计算，避免期限之间覆盖率不同造成假比较；当前执行信号年龄仍单独报告，未将IC解释为净收益。新增`portfolio_exposure_diagnostics`从每日日终`positions.parquet`读取实际持仓，使用同日有效且已可用的universe `tracking_group`，以独立账本权益为分母，分别报告最大ETF/分组权重、同组多ETF数量、现金/应收/收益权重，并逐日核对位置价值与独立权益。PIT证据未验证或持仓分组缺失时分别bypassed/partial；分类从不按当前状态向历史回填。已在`paired_report.development_diagnostics.signal_to_execution.by_fold[].portfolio_exposure`内同时保存baseline/candidate。

真实只读审计：[plan18-w05-pit-exposure-v1](../artifacts/research_audits/plan18-w05-pit-exposure-v1/portfolio_exposure_report.json)，来源是五份既存Alpha101 v2配对报告与冻结snapshot仅至2025-12-31的PIT universe视图。75个fold/seed配对、150个基线/候选侧、18,270个日记录全部完成且账本权益全部对齐；192个输入路径哈希复核无变化，holdout_values_read=false。基线/候选最大分组权重的逐run峰值中位数分别30.61%/29.18%，最大峰值均53.85%；候选与基线日期重叠、不同种子及候选公式，不是因果效果或择优依据。审计报告SHA256 `bad5c81a7a712c0425be7506af42e02ba9f44b0a79cfb4fc10bf633feb435055`，源码身份`f698510ad4e82116a597b7381aecc009a505af8a0d1aab0f3e581a4c65f000b6`。定向诊断/反馈测试28 passed，真实Qlib配对Docker集成1 passed、16 warnings；最终全量测试记录另行更新。

W05仍有边界：现有正式候选只在固定5日标签与月中/月末执行协议下计算净收益/成本；1/5/10/20日是共同样本上的预测IC衰减，不是四套可比的成本后组合回测。要比较实际期限策略需另建冻结的策略/标签/组合版本和匹配baseline，不能从现有IC或按信号年龄分组成本推断最优期限。

## 2026-09-26 W04真实筛选复核与全量回归

新规则下的冻结快照run：`artifacts/library_screening/plan18-alpha101-screen-v2`，状态completed。新ID防止覆盖v1；比较v1/v2完整验证manifest files均通过，v2屏幕身份的`code_hash`与screen-v2当时运行源码一致：`e8d2f04ee9ea863c0f3c762074fb1f7e8d3dd532ac4b656cb18c2b1287180588`。之后W07及后续补充有独立源码hash和测试记录，不回写screen产物身份。20个定义中17项技术通过、3项因覆盖不足`not_scored`；20项中5项进入shortlist（#40、#44、#14、#3、#55），与v1名单/hash完全一致。0正式评价、0外部调用、未读holdout。screen run manifest hash `210548c9153acaa67fc17d6580c5e43ef58d242fd47a5a99aef6169d56187138`。

`correlation_diagnostics.json`共680条（17选2×5折）折/因子对记录，诊断完成，输出两个所有五折稳定的相关簇：#6/#14日级中位Spearman约+0.917至+0.922；#33/#101约-0.921至-0.933。它们不改变筛选或候选资格；v1/v2 shortlist相同，表明这次信息补充不是另一次方向/窗口调参选择。

最终源码回归：`artifacts/research_audits/plan18-tests-v9/unit-results.xml`，929 passed、6 warnings、86.29秒，退出码0；JUnit SHA256 `f8ca6d070f4e5208c5b88810fdf5e772ce34a75e5b6f0aa52296569ab3d5cd8e`。paired真实Docker与RDAgent回放集成：`artifacts/research_audits/plan18-tests-v9/core-integration-results.xml`，2 passed、16 warnings、66.23秒、退出码0；SHA256 `fbbd954134db8ce9f234c54887d8db0610c62718c3bf994fde02c7adf510fd1c`。`git diff --check`退出码0，仅有已知LF/CRLF提示。上述不含留出或付费调用。

## W07 单次物理dispatch与失败usage落账（2026-09-26）

RDAgent transport此前会对疑似连接失败再次启动provider子进程，但错误文本无法证明第一次没有被供应商接收。现改为最多1次物理dispatch；在子进程前原子写入`dispatching`意图，缺响应统一报告uncertain且禁止自动重发。`GuardedLLM`在运行根写不可变`dispatch_policy.json`，固定运输身份、付费/费用状态、物理调用策略、单次超时和输出上限。失败attempt的token/usage为unknown时也会进入`billing.json`，使`usage_summary()`计入分母；provider返回后本地校验失败，也保留已观察usage，费用仍按账单状态对账而不伪造。

专项：`tests/unit/test_budget_llm.py tests/unit/test_provider_config.py tests/unit/test_research_campaign.py`，39 passed；其后加入预dispatch marker断言的`test_budget_llm.py`复跑为22 passed。覆盖含糊连接失败仅一个子进程、重试时ledger拦截、预先dispatch状态、策略manifest篡改/漂移拒绝、不确定调用provider token缺失仍计为unknown、已收到但超输出限值时usage仍保留。未执行真实provider调用。

限制：该实施把RDAgent自身LiteLLM自动重试关闭并将transport最大attempt设为1；仍没有按candidate汇总hypothesis/proposal/code多个逻辑stage的统一物理dispatch限额，也没有整个campaign的墙钟停止时间配置。已有session repair checkpoint上限仍为两次但尚未进入统一policy manifest；以上必须在后续继续实现，不能据此宣称W07完成。

### W07 replay初始化回归修正（2026-09-26）

真实RDAgent多trial回放集成发现：CLI先以默认transport创建研究会话并写策略，随后controller才切换ReplayTransport，造成未发生物理provider dispatch的本地回放被错误判为不可变策略漂移。修正为两部分：CLI读取replay后先以稳定ReplayTransport初始化session；`GuardedLLM`延迟到首次真实dispatch才持久化策略，已存在策略仍在构造时和dispatch前校验不变；`ReplayTransport`明确标记为本地回放，不生成provider物理dispatch策略manifest。它仍按replay自身的request identity及ledger完成确定性离线调用，不改变真实provider的“不确定不重试”边界。

验证：`tests/unit/test_budget_llm.py` 22 passed；`tests/integration/test_rdagent_research.py` 1 passed（含中断后恢复、多trial、registry和反馈检查）；核心集成组合`tests/integration/test_paired_research.py tests/integration/test_rdagent_research.py` 2 passed、16 warnings、67.63秒，JUnit SHA256 `497D4D74BCCB5A7ADD1898722EAAE4E40806778976DE886664ED3029A2BD8D5D`；全量`tests/unit` 930 passed、6 warnings、83.87秒，JUnit SHA256 `E95F848FA611B75895754F25900F98C7796C7B8AE68128E276271A2BE88E3644`。本次全量源码身份`code_hash()`=`cd861f84e8b5201405b5723379de31c4af15b8756cb547ad22727b2645ae1ef6`；测试全在本地/合成输入上，无provider dispatch、无holdout读取。`git diff --check`退出码0，只有工作树LF/CRLF转换提示。

本补修只关闭 replay 策略初始化的集成回归，不代表W07整体完成：当时跨stage dispatch、repair策略仍待补；墙钟停止和provider费用/usage对账仍未完成。后续 per-trial预算实现见紧邻下节；也不把工程验收等同于正式因子接受、独立holdout、前瞻OOS或投资准备。

### W07 per-trial dispatch与repair预算（2026-09-26）

`ResearchPolicy`新增`max_dispatches_per_trial=5`及`max_repairs_per_trial=2`，二者随冻结研究协议保存；LLM dispatch policy manifest也记录这两个值。controller以`<run_id>:<trial_index>`为dispatch scope；`BudgetLedger.reserve`在原子锁内统计该scope已reserved/uncertain/completed的逻辑dispatch，超限在物理调用前拒绝。因为本系统proposal契约每trial严格一个因子，这个scope同时是该候选边界；uncertain/成本未知照样占额，缓存命中不制造新dispatch。repair上限从session硬编码迁为策略配置，且现有checkpoint继续保存stage集合与已用总数，恢复不会清零。默认5次覆盖hypothesis、proposal、code和最多两次repair；如repair配置更小则总dispatch不会自动补足，若业务要改默认值必须新protocol/run身份。

验证：`tests/unit/test_budget_llm.py tests/unit/test_research_code_repair.py tests/unit/test_research_campaign.py` 39 passed；真实RDAgent本地Replay多trial/中断恢复集成1 passed；核心集成`tests/integration/test_paired_research.py tests/integration/test_rdagent_research.py` 2 passed、16 warnings；全量`tests/unit` 932 passed、6 warnings。工件分别是`artifacts/research_audits/plan18-tests-v14/w07-budget-targeted.xml`（SHA256 `6E7B0ACEFE91C97DD590927C6C3E54FD0E1EDABC47485309DF24975CC8CD6AA1`）、`rdagent-integration.xml`、`core-integration.xml`（SHA256 `822A3B0F21B70E2D15198CB222B9943D93CDC761291857E4453D8EEA903DE72A`）与`unit-results.xml`（SHA256 `0B87157C5E45066ED1BDF3AB6A72558C53411F7720FB748A49BC3B48E5A48DB6`）。全量测试时源码身份`code_hash()`=`68a5a3726f46485871d277c0ae15996b18826bc473e93b99fa4523595f08ce7d`；`git diff --check`退出码0，只有既存LF/CRLF提示。未发provider请求、未读取holdout。

仍未闭合：账本目前保存本地观测到的usage/费用，未连接供应商对账凭证；campaign超时会阻止新的trial/dispatch并在worker返回后的控制点停止，但不会强杀已启动的Docker/worker。因此A17/A18及W07不能标为完成。

## 2026-09-26 W06 下一假设证据约束

`build_prompt_context`仅在存在**同协议可比、正式经济状态为rejected且包含明确decision reason code**的历史卡时，向hypothesis请求加入允许的`factor_id/reason_code`链接清单及unknown不得当作事实的约束。`ETFHypothesisGen`要求响应精确引用一个允许链接；不匹配视为质量错误，仅可走既有一次hypothesis repair，修复请求仍带同一允许链接约束。校验通过后把`Evidence link: <factor_id>/<reason_code>`写入Hypothesis.reason，使后续proposal及已提交研究卡保留可追溯链路。技术失败、不可比结果、缺reason code或unknown缺项不会被提升为经济拒绝，也不触发此强约束。

实际路径测试`tests/unit/test_llm_factor_v1.py` 27 passed：包含adapter→GuardedLLM→ReplayTransport真实接收请求、风险/成本/换手原因到达、citation严格匹配、非法引用进入受限repair且repair仍收到约束、技术失败/无reason code不触发经济标签。全量`tests/unit` 937 passed、6 warnings，JUnit `artifacts/research_audits/plan18-tests-v17/unit-results.xml` SHA256 `2AB692DA1BF4CB9AF34F32424A97BA13E8A0F833B6BDF158591B085E0D9A5B30`；核心paired与RDAgent replay集成 2 passed、16 warnings，JUnit SHA256 `2EDB6EB652B31BFA852F28CC5DA853C0BDC2231909B336FEC0D724EE33C930E2`；本次源码hash `0e55bf04b8c80af494f64ba055b2de830cf30aedc325a6cf575be6964c030442`。`git diff --check`退出码0，仅有既存LF/CRLF转换提示。未发起provider调用、未读holdout。

W06仍有一项台账级验收未闭合：尚无针对所有已提交trial的反馈字段覆盖率报告，逐条证明风险、成本、换手、消融、执行链路和unknown原因中哪些已送达/哪些缺失；当前测试覆盖的是代表性强制场景，不外推为全量覆盖。

## 2026-09-26 W01 旧资格报告消费兼容

消费端检索确认生产代码中 `audit_research` 是 `first_loop_report.json` 的阶段资格重算/发布消费端。现在它对新版 report 若提供`qualification_report`及SHA256，会验证文件在source run信任根内、hash一致、schema/snapshot身份一致，且各stage status与当前snapshot、candidate evaluation重新计算结果一致；部分字段、hash漂移或stage状态矛盾均`IntegrityError`。若旧report没有该链接，则派生`legacy_recomputed`审计状态，并显式`legacy_booleans_used=false`；顶层固定`formal_g0_passed=false`、`holdout_evaluated=false`、`investment_accepted=false`均不用于推出数据失败或改变资格。源report与source run受保护hash保持不变。

验证：`tests/unit/test_research_audit.py` 5 passed；已有端到端审计夹具添加三个旧false字段，结果仍由原始data_quality证据给出`data_qualification=unknown`而非failed；另测试新链接可核验、旧false不参与判定、linked report hash漂移及重算stage矛盾拒绝。全量`tests/unit` 938 passed、6 warnings，JUnit `artifacts/research_audits/plan18-tests-v18/unit-results.xml` SHA256 `C7C54C9F551BC56EEA9EA44D789373E70425F81C0C3DEE14AA7EEC7A3939DD27`；核心paired/RDAgent集成2 passed、16 warnings，SHA256 `DE644CEFD713399ADCC982706EFDD2C4EF33062149A829E0C7CF63E59D96DA8D`。本次源码hash `cf174419f01c57f088637ba19b0d4c120ee41343acd3edd67b4a3c949a2532a8`；`git diff --check`退出码0，仅有既存LF/CRLF提示。未发provider调用、未读holdout。

后续真实旧报告复核：`artifacts/research_audits/plan18-w01-status-v1/audit_report.json` completed，源码身份`f698510ad4e82116a597b7381aecc009a505af8a0d1aab0f3e581a4c65f000b6`；R05 qualification link如预期`legacy_recomputed`、`legacy_booleans_used=false`，资格阶段为data `unknown`、factor `rejected`、holdout `ineligible`、holdout evaluation `not_run`、prospective `not_started`、investment `not_ready`。保护的R05/snapshot/baseline文件前后hash一致；15个风险配对均partial而非伪装完整；external calls=0、training runs=0、holdout values read=false。W01/W03/W04专项`tests/unit/test_research_qualification.py tests/unit/test_research_audit.py tests/unit/test_alpha101_library.py tests/unit/test_library_screening.py`：80 passed。原始R05及配对工件未被修改；Alpha101 v2正式终审五项全reject保持不变。

边界：此项证明项目自身research audit CLI对新旧report的兼容，不证明外部下游脚本、用户手工流程或其他应用未消费这些旧布尔字段。W01尚未关闭全部外部消费端盘点。

### W07 campaign墙钟期限（2026-09-26）

`ResearchPolicy.max_campaign_wall_seconds`新增默认8小时的正整数上限，随protocol/config hash冻结；campaign新建时将其写入`campaign.json`，既有未设置该字段的历史ledger仍可由只读审计兼容读取，若显式复用同ID并要求不同期限则拒绝。`trial_started`事件记录纳秒时间戳，ledger摘要给出elapsed/expiry。控制器在每个新trial前检查，LLM dispatch前调用campaign deadline guard；超时保留in-progress与usage证据、返回`paused_budget/campaign_wall_clock_limit`，不会退回名额或把费用未知改零。重启恢复读取原始campaign期限，不重置计时。

测试：`tests/unit/test_research_campaign.py tests/unit/test_budget_llm.py tests/unit/test_research_code_repair.py tests/unit/test_first_loop_reuse.py tests/unit/test_first_loop_readiness.py tests/unit/test_research_audit.py`，67 passed；包含计时器控制的过期拒绝新trial/dispatch、campaign期限不可变、dispatch限额和repair checkpoint恢复。与上一节已记载per-trial预算的源码合并后，全量`tests/unit` 934 passed、6 warnings（JUnit SHA256 `BC497649E3E5674506632B315C23498C302753019D9A9162C84D38A2B8551F04`），核心`tests/integration/test_paired_research.py tests/integration/test_rdagent_research.py` 2 passed、16 warnings（SHA256 `FF3BE08F1CD0A68B474B2EBE0AD2FFD596A11D739B7D644F2076A053F64315D3`）；源码hash `4a02a13b56ad807724e2265fc8c0e1571333f4b621e23cb872a845e6155b6b74`。定向JUnit `w07-limits-targeted.xml` SHA256 `C7B989DFBD3C86E88A4C783BBA80983FFA158C7FE5AAD9FAAE5B09A15A6D4045`。`git diff --check`退出码0（仅CRLF提示）。未发起provider调用或读取holdout。

边界说明：campaign的硬截止阻止新attempt和新provider dispatch，但不终止截止前已进入的模型/容器子进程；这些子进程继续受各自的`timeout_seconds`限制，返回后controller在下一控制点保留为未完成，不发布为研究接受。要实现可抢占的进程树终止和Windows/Docker跨平台强杀语义，需要另行验证，当前不宣称具备。

期限边界的worker返回复核：controller现还在coder执行、paired runner执行及feedback生成后重新检查期限；若worker在campaign过期后才返回，会进入`paused_budget`并保留in-progress，不晋级feature、不提交trial结果。过期前已完成的provider响应与成本/usage仍留在账本。全量回归对应最终补丁源码hash `e30c98fc14fa5fb2033cae8bb69996805ef4fbea278cea42b9f5e4f931624074`：`artifacts/research_audits/plan18-tests-v16/unit-results.xml` 934 passed、6 warnings、JUnit SHA256 `9D8D0BF733ADB382F7A8BDEABB7D5A685357C74101D750BF86376DE6DCEB44EE`；核心集成`artifacts/research_audits/plan18-tests-v16/core-integration.xml` 2 passed、16 warnings、SHA256 `7696135F76387D76681D4CE09E68259EBA9DE186895B2D6054157915288FB78B`；期限/恢复定向集成前置单测67 passed。当前验收覆盖过期关口与正常整条集成，不含真实worker耗时跨越8小时的端到端演练，也不含可抢占容器强杀语义。

### W06 量化成本/消融/执行缺项进入反馈卡与下一假设（2026-09-26）

反馈schema升级为`feedback-summary-v3`。除了原有拒绝原因、配对风险与信号→执行汇总，研究卡现在投影成本压力逐折/seed的基线/候选超额收益、增量、成交成本及已知样本数；投影分组消融状态、拒绝原因和配对折/seed差值；信号诊断增加rank比较数、缺预测数和缺信号年龄数。压力指标不完整、执行证据非completed、消融结果不可用、质量检查未完成等分别写入`unknown_evidence`，保持未知与已测失败的区分。`stage-context-v3`提示词只压缩保留这些证据，不重新评分或把未知改成事实；同协议、可比、正式经济拒绝的证据链接约束继续生效。

真实请求接收链测试先由结构化评估结果调用`feedback_summary`生成研究记忆，再经过card、提示词、ETF hypothesis adapter、`GuardedLLM`和`ReplayTransport`，断言量化压力中位差值/成本、逐折seed计数、消融失败和未知字段均确实出现在下一假设请求里；provider未调用。定向`tests/unit/test_development_feedback.py tests/unit/test_llm_factor_v1.py` 45 passed。全量`tests/unit` 938 passed、6 warnings，JUnit `artifacts/research_audits/plan18-tests-v20/unit.xml` SHA256 `9217030B293351261B7DE5F471B18086C8EAAE484E90BF6C8BA5F38CDC3D5F4E`；核心配对/RDAgent回放集成2 passed、16 warnings、67.24秒，JUnit `artifacts/research_audits/plan18-tests-v20/core-integration.xml` SHA256 `53DE7A3E97CF71665A21D22633979472B213DC9AA7A95C369D022C5B210E6A4C`。源码身份`code_hash()`=`1dd59466a489b002f22cb3818e1ead75e6906855b5cbc79551c0986af61649b7`；`git diff --check`退出码0，仅有既存LF/CRLF转换提示。未发起provider调用、未读取holdout。

截至此节的v20版本仍未完成历史trial全量对账；后续关闭情况见下节。离线回归通过也不代表formal候选通过、holdout通过、前瞻OOS通过或投资准备就绪。

### W02 风险检查与卖单意图—成交关联（2026-09-26）

新生成的`risk_checks.json`在每个交易日均明确记录`risk_order_intents`与`risk_order_blockers`：未检查/非调仓/缺信号日写空列表；风险触发时记录风险卖单的证券、方向、请求数量、下单日和信号日，并列出因不可卖而保留的持仓及原因。日级诊断按交易日、证券、卖出方向关联`trades.parquet`，核对请求数量，并报告匹配/未匹配、成交数量、部分/未成交状态和失败原因；重复成交键、数量不符或错误schema fail closed。缺少意图或阻挡字段的旧回测仍标为`unknown_legacy_trace`，配对日级风险归因降为`partial`。连续回撤事件另给出首个后续检查、风险卖单尝试、阻挡原因和首个可观察到的实际成交日；不会把尝试日或下一交易日冒称为`earliest_legal_exit`，该字段仍为`null`，直到有充分的交易规则证据。

对既存Alpha101 v2做只读复核：5份paired报告、150条基线/候选折×seed回测、18,270个日级记录、92个风险触发日和218段收盘超限事件；18,270条历史日记录全都缺少风险订单意图/阻挡字段，因此均保留未知关联。未改写其风险记录、成交、配对报告、冻结快照或协议，也未启动新campaign或读取holdout。该结果证明的是观测到的基线/候选风险和历史证据缺项，不足以评价“每日风险检查”策略效果。

风险归因测试覆盖显式卖单的全额/部分/未成交、失败原因、不可卖阻挡、旧schema未知、重复成交拒绝、风险事件日期分离，以及真实Qlib配对回测在所有日记录输出可验证关联。最终代码版本全量`tests/unit`：944 passed、6 warnings；定向`tests/unit/test_daily_risk_attribution.py`：7 passed；Docker/Qlib配对集成`tests/integration/test_paired_research.py`：1 passed、16 warnings。`git diff --check`退出码0（有工作树既存LF/CRLF提示）。provider dispatch=0，holdout未读取。

范围限制：此次只补风险证据的可追溯性，不改变月度检查/回撤阈值/交易执行策略。策略当前以决策日开盘标记持仓并在同一开盘参考价假设成交；本诊断不证明开盘信息先于成交可用。没有风险卖单时不合成反事实的“最早退出”，不能保证回撤硬上限；若要改为每日风险执行，需独立冻结portfolio/protocol身份、定义开盘信息时序和重入规则，并新建匹配baseline后重测停牌、跌停、跳空、流动性、现金与应收。

### W06/A15 已提交trial反馈覆盖审计与历史记忆重建（2026-09-26）

新增`audit-feedback-coverage`只读入口，以`research_memory/v2/sources.json`显式注册源和每个session的`checkpoint.json`为权威，不依赖可能陈旧的派生索引。逐条核对committed trial文件hash、card hash、不可变协议fold/seed矩阵；重新校验evaluation/paired/baseline/candidate/ablation引用哈希，并在审计期间复验已读source hash。每项分列原始持久化卡覆盖与当前记忆投影覆盖，status区分`present`、`partial`、`not_transmitted`、`upstream_partial`、`missing`及`not_applicable`。未正式评价的技术失败不会被当作经济拒绝或制造配对指标。

为修复历史卡，而非改写历史trial，`ResearchMemoryIndex.rebuild()`在protocol身份一致且候选身份可对上的前提下，从提交trial中结构化feedback确定性重建旧v2反馈为v3；验证有效的evaluation evidence后，只将文件名和SHA256加入prompt投影、不泄露本地路径。技术失败的`failure_category`/原因也进入prompt，仍与正式经济拒绝分开。真实审计前后均核实20个显式source、16个checkpoint已提交trial，原始trial/checkpoint/protocol没有改写。

审计工件：`artifacts/research_audits/plan18-feedback-coverage-v6/feedback_coverage_report.json`，16/16卡片hash有效；13条有正式评价的候选在记忆投影中逐折/seed、基线/候选风险及增量、压力收益/费用、换手、消融、信号执行、未知原因和评价hash均为present；其余3条为技术/质量失败，所有经济评价字段明确为not_applicable且3/3技术失败原因送达。与之相对，旧持久卡对13条已评价候选的成本压力、消融、未知原因和评价hash此前全部未传递；本次通过只读重建补入派生索引。该报告仅覆盖明确登记的20个source，不推断未登记人工或外部记录。

验证：定向反馈/记忆/审计/请求测试60 passed；最终全量`tests/unit` 942 passed、6 warnings，JUnit `artifacts/research_audits/plan18-tests-v22/unit.xml` SHA256 `80AF94116B374D1143D83D980152E1FA4D0134F4FD974861C41571538147C93F`；核心`tests/integration/test_paired_research.py tests/integration/test_rdagent_research.py` 2 passed、16 warnings、82.02秒，JUnit SHA256 `7710F816BDE8BDF1BB9BDB7B826701EBFE63693957DAE3FC95A9F2797581C48E`。审计报告`feedback_coverage_report.json` SHA256 `D57575F71A4F0628C1648FB6C0B7F2671568567F0A9366CF11E131C769E80637`；其`source_code_hash`与当前`code_hash()`一致，为`4728c8714cd7c6f035b0a6f910ee1b0f8b7fba11e3455223fb9b681096b3d4e5`。`git diff --check`退出码0，仅有工作树既存LF/CRLF转换提示。全程provider dispatch=0，holdout未读取。

### 当前冻结源码全量单测复核（2026-09-26）

运行完整单元测试：`E:\tools\anaconda\envs\qlib_zhengshi\python.exe -m pytest tests/unit -q`，结果`950 passed, 6 warnings in 85.76s`，退出码0。6条均为现有pandas FutureWarning，涉及补充数据拼接及universe布尔状态`fillna`行为，没有失败或错误。

本次源码身份`code_hash()`=`eac2c70a168241ae2928902bfd3d0e7faacc22a70fcabe7a6a96b2055f6b8c75`，与正在执行的冻结h=1 formal run身份一致；因此该回归覆盖当前工作树源码，但不能替代h=5/10/20实跑结果。复核时campaign父进程PID 21012、worker PID 13728均存活；h=1状态仍为running，#55处于candidate阶段。未改动冻结快照、筛选manifest及既有h=1终态报告。台账追加后`git diff --check`退出码0，无空白错误；Git仅提示工作树LF/CRLF转换。

**#55运行进度（2026-09-26 06:31本地时间）**：活动worker的`progress.jsonl`显示候选侧seed 44已完成A–D四折，E折LightGBM已启动；5/5模型计数中前4项已完成。对应paired `progress.json`为`running`，最近一次写入06:30:56；worker stderr在06:31仍有Qlib数据/训练日志，父/worker进程PID 21012/13728均仍存活且可响应。该证据取代此前将#55进度停留在seed 42/43的滚动描述；尚未达到paired终态，不判定#55结果。

**#55进入汇总评估（2026-09-26 06:31本地时间）**：最新paired `progress.json`变为`component=paired_research, phase=evaluating, completed_kinds=3, status=running`，`progress.jsonl`更新时间06:31:47。尚未生成可确认的终态`evaluation.json`/`paired_report.json`，继续等待同一运行句柄，不重启。

**#55及h=1终态（2026-09-26 06:33本地时间）**：Alpha101 #55正式配对`completed/rejected`，15个fold/seed行；理由为`ablation_not_confirmed`、`absolute_risk_limit`、`cost_pressure_return_limit`、`cost_pressure_risk_limit`、`drawdown_deterioration`、`no_majority_fold_increment`、`seed_instability`、`turnover_deterioration`。其中9/15候选回撤超过12%，9/15 2倍成本回撤超过12%，5/15 2倍成本超额收益低于-3%，最大候选回撤17.47%、最差2倍成本超额收益-6.11%；相对基线的超额收益增量仅1正、3负、11零，A–D折中位数均为0、E折为-0.899%，`volume_price`组消融拒绝。evaluation SHA256=`bc8a4ce15a99298019c83dab808eaba6cd8497a2b58ac6f83521da6f1c3c08e8`，paired report SHA256=`a98a742de369a137c5a294fd8061b3451a4025fbc9572569b176b9228789f9d1`，run manifest SHA256=`051c7a609e9c3f1dbcbef8fdc4583118f80c7d6bb2372e64a27ddae2c67f2519`。h=1五个shortlist因子至此全部formal rejected、工程失败0；`formal_evaluation_report.json` SHA256=`4290abc667a0a68aa3e27bec646a9a4d9b32090b6bf67f20a5aebadb9acc7a1d`。外层已切换到h=5并保持running；h=5至h=20仍待正式终态，不能按h=1挑期限或候选。

**h=5启动（2026-09-26 06:34本地时间）**：独立campaign `plan18-alpha101-horizons-v1-h5`状态`running`，第1/5候选`alpha101_040_etf`已启动；其配置沿用同一冻结snapshot和候选shortlist，将标签期限设为5。外层总run `plan18-alpha101-horizons-v1`仍为`running`；不得将独立h=5协议结果与h=1合并择优。

**h=5候选#40进度复核（2026-09-26 06:40本地时间）**：活动paired仍在基线侧A折seed 42、`equal_weight_pool`辅助组合的2倍成本压力回测；最近的`progress.json`时间为06:39:02，尚无终态evaluation/paired report。父进程PID 21012与worker PID 23612仍存在且worker CPU时间继续增加，stderr最近写入06:39:04。状态不是卡死或终止证据，继续保留同一run。

**h=5折进展（2026-09-26 06:42本地时间）**：相同paired运行已从A折推进到基线侧B折seed 42，`progress.json`更新时间06:42:03且`progress.jsonl`持续增长；父进程PID 21012、worker PID 23612继续存活。候选#40仍未完成正式配对，不提前总结其经济结果。

**h=5协议身份核验（2026-09-26）**：直接检查该候选实际`protocol.json`而非依赖run名称：`label.horizon=5`、5个开发折、model seeds 42/43/44、`stress_min_excess_return=-0.03`、`holdout_independent=false`、holdout边界`2026-01-01`；snapshot id=`8b384b5d9f8fa10d3d7b6aebbf11fd9463dc76cc9fe203d38dfc6f5ad4322831`，source code hash=`eac2c70a168241ae2928902bfd3d0e7faacc22a70fcabe7a6a96b2055f6b8c75`。campaign formal manifest为`external_calls_allowed=false`、`holdout_read=false`、尝试上限5，SHA256=`c16b9571d1d8347bd3dda8a8194f21d838a5ac44b46c72c9f972739099d1ece9`。所以它是预注册的独立h=5开发协议，不是对h=1结果的标签后改；仍不得解释为独立OOS。

**h=5进展复核（2026-09-26 06:44本地时间）**：paired `progress.json`显示基线侧B折seed 42进入`equal_weight_pool`辅助组合的2倍成本压力回测（更新时间06:44:31），`progress.jsonl`继续增长，worker stderr更新至06:44:32。PID 21012/23612仍存活；#40没有终态评价文件，不提前归类。

**h=5折推进（2026-09-26 06:47本地时间）**：同一paired run已推进至基线侧C折seed 42的`manual_momentum`辅助策略回测，`progress.json`更新时间06:47:40、worker stderr更新时间06:47:41；父/worker PID 21012/23612均存活。候选#40尚无配对终态文件。

**h=5成本压力进度（2026-09-26 06:50本地时间）**：paired进度进入基线侧C折seed 42的`equal_weight_pool` 2倍成本压力场景（`progress.json`更新时间06:50:41，`progress.jsonl`增至15,828字节）。h=5状态仍`running`，父/worker仍存活；未产生可判定经济结论的终态报告。

**h=5继续推进（2026-09-26 06:53本地时间）**：同一候选#40的基线侧已进入D折seed 42 LightGBM模型（`completed_models=3/5`，阶段`model_started`），paired `progress.json`/`progress.jsonl`最近写入06:53:31；campaign父进程PID 21012与worker PID 23612均经进程表复核存活。h=5整体仍running，候选无终态。

该进度随后于06:54:17推进至D折seed 42的`manual_momentum`辅助基线回测；paired状态继续为running。该更新只说明流水线前进，不构成模型训练终态或因子结论。

**h=5基线矩阵推进（2026-09-26 07:00本地时间）**：候选#40基线侧进入E折seed 42 LightGBM训练（`completed_models=4/5`，`model_started`），`progress.json`更新时间07:00:12，stderr及模型/数据manifest在07:00仍有新写入。父/worker PID 21012/23612存活；paired及h=5 campaign仍running，无终态evaluation。

**h=5基线侧E折推进（2026-09-26 07:01本地时间）**：E折seed 42 LightGBM后续已进入`equal_weight_pool`辅助基线回测（`progress.json`更新时间07:01:24）；paired仍running，尚无`evaluation.json`或`paired_report.json`终态，不提前判定。

**h=5 E折baseline成本压力推进（2026-09-26 07:05本地时间）**：同一#40配对运行的E折seed 42 `equal_weight_pool`辅助基线base回测已完成，随后进入2×成本压力回测；`progress.json`与`progress.jsonl`更新时间07:04:53，辅助回测缓存同时写入`ledger.parquet`、`execution.parquet`、`exposures.parquet`、`risk_checks.json`、`decisions.json`及现金/应收/持仓/成交等账本文件。父PID 21012与worker PID 23612仍存活且响应，CPU时间继续增长；h=5仍running，尚无候选终态evaluation或paired report。仅记录流水线进展，不据此推断因子表现。

**h=5 campaign只读账本审计（2026-09-26）**：调用生产`CampaignLedger.audit_view()`对活动ledger做一致性读取，事件链有效，2/2事件（`trial_started`、`proposal_generated`），head=`f608889b2d657666e61c3599f621c77a42ad9cb88dd8dd42c58ad66df7390ea7`。已占尝试1/5、唯一定义1、open attempt 1、已完成正式评价0、8小时期限未到；usage状态`partial`且唯一原因`unresolved_trial_usage_slots`。账本没有provider调用/usage记录；manifest同样禁止external calls。`cost_known_subtotal=0`仅是当前尚无provider记录，不代表供应商收费实测为零。该审计不改ledger或活动run。

**h=5候选#40终态与后三项推进（2026-09-26 08:00本地时间）**：候选`alpha101-68c2cbb90c2b7fb7db5821c3-alpha101_040_etf`正式完成、rejected，完整15折×seed行；终态原因包括`ablation_not_confirmed`、`absolute_risk_limit`、`cost_pressure_return_limit`、`cost_pressure_risk_limit`、`drawdown_deterioration`、`no_majority_fold_increment`、`seed_instability`、`turnover_deterioration`。逐行审计显示候选最大回撤>12%为12/15，2×成本回撤>12%也是12/15，最差2×成本超额收益=-5.246%；配对超额收益增量5正、3负、7零，波动率因子组消融拒绝。evaluation/paired/manifest SHA256分别为`4efce13a49b3e3d68fbb068c01e61e9b9f8fd85f593ef74725d0355775af4246`、`e61ab72880378a9637736a4aff13378749b6c6bfc69443d0e90507f67861d7fa`、`72ae24c1734a25af4fa5e0566c5cca8203d66f0c7ca95093943eae70e3a51ce6`。#44与#14亦各有15行正式拒绝：#44配对增量0正/2负/13零、12/15候选及压力回撤超12%，1行压力超额收益低于-3%；#14增量4正/2负/9零、同样12/15回撤超12%，未触发压力收益低于-3%，但仍因消融、风险、增量/稳定性和换手等联合门槛拒绝。两者evaluation SHA分别为`8b4b2b98d0701a8cb814e4b77868495f36e37d5a62b992ea64cc04dc976894c9`和`7c9c13b887c5f872a5c8eef55b6e32412ead71290397627dbc888c186dac8436`。截至08:00，h=5 campaign第3/5候选#3正运行（worker PID 23280，progress指向candidate_started，trial_index=3）；h=1仍为五项全拒绝，h=5其余#55尚未开始，h=10/20未开始。父进程PID 21012持续存活；未读holdout，也未改冻结源码/快照/协议。

**h=5 campaign后续只读审计与#3折进度（2026-09-26 08:02本地时间）**：通过生产`CampaignLedger.audit_view()`复核后，账本链11事件有效，尝试4/5，唯一因子定义4，3个候选正式提交且全为rejected，第4项`alpha101_003_etf`保持open；剩余时限充足（8小时上限未到）。provider usage覆盖为null、实际可知费用小计`0`仅代表没有provider记录；usage仍`partial`，原因仅`unresolved_trial_usage_slots`，不是实测免费。#3 paired `progress.json`已到候选侧C折seed44 LightGBM（2/5模型完成），更新时间08:02:16；父PID 21012、worker PID 23280均存活且CPU继续增长，h=5状态running。运行protocol中的snapshot与源码hash仍分别为`8b384b5d9f8fa10d3d7b6aebbf11fd9463dc76cc9fe203d38dfc6f5ad4322831`与`eac2c70a168241ae2928902bfd3d0e7faacc22a70fcabe7a6a96b2055f6b8c75`，后者经当前`code_hash()`实算一致。只读审计及状态采样未改campaign事件或run工件。

**h=5候选#3终态与#55启动（2026-09-26 08:06本地时间）**：候选`alpha101-e42a30f1830f911b7fa72ae0-alpha101_003_etf`正式rejected，15个折/seed完整；原因同样覆盖消融未确认、绝对风险、2×成本收益/风险、回撤恶化、无多数折增量、seed不稳定及换手恶化。候选和2×成本压力回撤>12%均为12/15，最大值分别17.64%与17.94%；1/15行压力超额收益低于-3%，最差-5.246%；配对增量仅1正、2负、12零，中位数0；`volume_price`组消融拒绝。evaluation/paired/manifest SHA256分别为`b53cc456a7e1876e9bc6b03668d27db747675522b41f150291eee0f185920698`、`f03f1ae13398a231e45def9382f12f00ba3893db4070e9ab633ab7f7b4bdc0e5`、`ea0bb724fea34b1f2c26a7d2746432329aec80154e490c1c1c910ea6cea75490`。外层随后正式登记最后的#55（trial 5/5，run `alpha101-e25b518f344e6ca0f9e848d1`）；h=5台账只读复核为14事件有效、5/5尝试、4个已提交且全部rejected、1个open，未达8小时期限。父进程PID 21012仍存活，#55配对worker尚未在该采样点出现；继续等待父进程分派/完成，不擅自重启或增加第6次尝试。provider费用/usage仍unknown/partial，holdout未读。

**h=5最后候选worker已分派（2026-09-26 08:07本地时间）**：#55的paired worker PID 26476于08:07:04启动，执行进程写入`experiment_worker/paired/started`；父PID 21012与worker均存活且响应。初始stderr只有Gym旧依赖弃用提示，没有研究异常；本条仅确认分派，不宣称候选进度或评价结果。源码仍冻结，h=5尝试数维持已声明的5/5。

**h=5候选#55矩阵进展（2026-09-26 08:17本地时间）**：候选侧seed42和seed43均已完成A–E折；seed44已完成A–C折并推进到D折LightGBM（`completed_models=3/5, model_started`），`progress.json`更新时间08:16:54；seed44余下折与最终消融/评估仍待完成。PID 21012/26476均持续存活且CPU时间增长，h=5与#55仍running，尚无evaluation终态。不因尚未完成的seed矩阵推断因子结论。

**h=5最后候选#55终态及h=10初始化（2026-09-26 08:20本地时间）**：#55`alpha101-e25b518f344e6ca0f9e848d1-alpha101_055_etf`已正式rejected，15折/seed行完整；拒绝原因包括`ablation_not_confirmed`、`absolute_risk_limit`、`cost_pressure_return_limit`、`cost_pressure_risk_limit`、`drawdown_deterioration`、`no_majority_fold_increment`、`seed_instability`、`turnover_deterioration`。候选及2×成本压力回撤>12%均12/15，最大分别18.13%和18.42%；1行压力超额收益低于-3%，最差-5.246%；配对增量2正、2负、11零，中位数0；`volume_price`组消融拒绝。evaluation/paired/manifest SHA256为`3dc110332681c67a24019f746dbf93d83095af5297f6b1b0bb80eec71120d761`、`ed873fd6afa284127d4991bbaf3520814dac3ba8584df59652ca4fa3dfcfda64`、`f9ceab1f28e6c837f13690be39c4025696b49cd6aa57b8d5352aae3faeeabdd5`。h=5正式总报告SHA256=`104f2ea9045b4bf468955e8988b061b64825f7fd3299691ccb6d929738ab4a3d`，5/5因子均formal rejected、工程失败0、holdout未评、investment readiness=`not_ready`；全5次提交、0 open、事件链15项有效。无provider dispatch；`provider_cost=not_applicable_no_provider_dispatch`且usage覆盖null，故这表示本campaign没有外部LLM计费调用，不代表其他实际研究成本为零。外层父PID 21012仍存活并已初始化h=10，status为running；h=10 manifest锁定相同snapshot/source hash、attempt cap 5、external calls关闭、holdout未读。h=20尚未开始，四期限结论仍不完整。

**h=10候选#40 baseline矩阵进度（2026-09-26 08:35本地时间）**：外层父PID 21012与paired worker PID 26028仍存活。paired已完成baseline侧B折seed42 `equal_weight_pool` 2×成本压力回测、C折seed42 LightGBM并进入C折`equal_weight_pool` base auxiliary回测，`progress.json`更新时间08:34:40。仍处baseline矩阵，无候选经济结论。

**h=10候选#40继续运行（2026-09-26 08:44本地时间）**：父PID 21012、worker PID 26028均仍存活且worker CPU时间继续增长；同一paired `progress.json`已推进至baseline侧D折seed42 `equal_weight_pool`辅助组合的2×成本压力回测，最后进度事件08:44:01。#40仍未进入候选矩阵，当前没有候选侧结果或终态报告；冻结源码、快照、baseline和campaign均未修改。

**h=10候选#40基线侧继续推进（2026-09-26 08:46本地时间）**：D折seed42的`equal_weight_pool` 2×成本压力辅助回测已完成，随后同一baseline侧进入E折seed42 LightGBM（4/5模型已完成，progress时间08:46:48）。父PID 21012和worker PID 26028仍存活、CPU时间增加；候选侧尚未启动，尚无#40正式经济结论。

该baseline矩阵目前推进至E折seed42 `equal_weight_pool` base辅助回测（progress时间08:48:02）；父/worker PID 21012/26028仍存活且CPU时间增加。该步骤仍属于基线侧，不产生候选IC/ICIR或经济验收结论。

## 2026-09-26 W04 IC/ICIR报告与门槛审计

用户确认正IC/ICIR代表正向候选，负IC/ICIR应作为反向信号候选而非直接淘汰；跨折选择“先汇报折级结果，暂不定汇总规则”。因此新报告必须逐折（模型结果再分seed）给出IC、RankIC、ICIR、RankICIR及有效日期数/覆盖，不把折或seed混池，也不能在汇总规则未定前给出候选级“信息门槛通过”。反向候选只有在方向由训练内层数据确定并冻结、随后由未参与方向选择的数据确认后，才可进入有效性判定；不得在查看验证折结果后临时翻转因子。该规则尚未纳入本次冻结campaign，不能追溯性重判既有正式结果。

代码审计发现`predictive_metrics`和`development_diagnostics`已有逐日期IC/RankIC、逐折因子均值与衰减诊断；但未生成ICIR，`evaluation.json`正式配对报告也未汇总这些预测统计。因而W04的IC/ICIR方向判断与主报告可见性目前**未实现**。ICIR拟定义为逐折日IC均值除以日IC样本标准差（`ddof=1`，不年化），RankICIR同理；有效日期少于2或标准差为0时输出`null`及原因。后续实现须使主报告内嵌折级统计或链接带SHA256的诊断文件，并覆盖缺日、零方差、样本不足、方向选择不泄漏及seed/折分隔测试。

只读后验演算示例：已拒绝的h=5 `alpha101_040_etf`开发诊断中，逐折主因子IC均值/ICIR（A–E）分别为A `-0.00928/-0.0743`、B `0.03191/0.2206`、C `0.01397/0.0802`、D `0.04896/0.2508`、E `-0.02759/-0.1340`。这显示折间符号不一致，只作为对既有开发诊断的补充计算，不构成预注册硬门槛、正式报告字段或候选重判；原因子仍以其正式拒绝结论为准。

本次只改工程规划和证据文档，未改源码、活动h=10运行、协议、快照或历史产物。h=10父进程PID 21012和paired worker PID 26028仍存活；IC/ICIR报告与硬门槛应在当前冻结campaign结束后按新源码身份实现和测试。当前跨折汇总规则未确定，所以完整candidate-level信息资格仍不可判定。

### IC/ICIR隔离实现原型与测试（2026-09-26）

为不改变上述冻结campaign，在`%TEMP%\etf-qlib-plan18-icir-20260926`对当前`src`/`tests`建立隔离副本，并在副本中实现IC/RankIC日序列统计：逐折报告均值、样本标准差（ddof=1）、未年化ICIR、有效/总日期数、横截面覆盖、正日占比及`positive`/`reverse`/`unknown`方向状态；样本不足或零方差时ICIR为null并给出原因。paired开发诊断保留每折/seed逐日模型序列；成熟因子筛选报告另外给出原始因子方向诊断，负向标记为reverse，不基于同一验证数据临时翻转；RDAgent紧凑反馈只传递汇总数值和方向状态，不传日期明细。

隔离副本测试结果：`test_predictive_metrics_icir.py`与`test_development_feedback.py`共21 passed；library screening/Alpha101测试67 passed；paired Qlib集成1 passed、16 warnings，新增断言确认主paired诊断报告含逐折因子日序列和分seed模型IC/ICIR字段；完整`tests/unit`为953 passed、6 warnings（88.63秒）。以上只证明隔离原型及其测试，不是当前主工作树实现证明；活动h=10源码身份仍保持`eac2c70a168241ae2928902bfd3d0e7faacc22a70fcabe7a6a96b2055f6b8c75`，原型补丁须待活动多期限campaign终态后审阅并合入，再在主工作树重跑定向/全量验收。原型测试没有provider dispatch，也未读取真实holdout。

**h=10候选#40的后续进度（2026-09-26 09:02本地时间）**：baseline侧seed 44已完成A–E五折及辅助组合，worker随后进入candidate侧seed 42；父PID 21012与worker PID 26028仍存活。此为首次进入候选计算阶段，不是评价结果；冻结源码、输入快照和baseline未修改。

candidate侧seed 42已完成A折（LightGBM及辅助组合），并进入B折LightGBM训练，progress时间09:02:53；父/worker仍存活。候选#40仍未形成完整15折seed矩阵或终态评价。

candidate侧seed 42随后完成A–E五折模型和辅助组合；seed 43已启动（progress时间09:05:30），父PID 21012/worker PID 26028仍存活。#40余下两个seed及最终配对/消融评估待完成，暂不推断候选质量。

**h=10候选#40 seed 43推进（2026-09-26 09:06本地时间）**：候选侧seed 43已完成A折，B折LightGBM训练启动（`completed_models=1/5`，progress时间09:06:22）；父PID 21012和worker PID 26028仍存活且CPU时间增长。#40尚无完整seed矩阵/配对终态。

seed 43随后完成B折LightGBM及辅助组合，并进入C折LightGBM（`completed_models=2/5`，progress时间09:06:54）。父/worker仍存活，候选仍无终态评价。

### IC/ICIR方向规则隔离验收更新（2026-09-26 09:20本地时间）

用户进一步明确：`IC<0`且`ICIR<0`作为反向指标使用，不作为淘汰条件。隔离原型已按训练段统计确定`sign=-1`，在未参与定向的验证段分别报告原始和反向指标；训练IC/ICIR方向不一致、样本不足、ICIR为null或零方差时，方向保持`unknown`，不强行翻转。跨折仍仅报告折级结果，不推导候选级汇总通过。该规则不回写已完成campaign的结果。

在隔离副本`C:\Users\lufen\AppData\Local\Temp\etf-qlib-plan18-icir-20260926`重新验收：完整`tests/unit`为`956 passed, 6 warnings`（91.36秒）；`tests/integration/test_paired_research.py`为`1 passed, 16 warnings`（25.33秒）。对应运行通过`PYTHONPATH=src`明确加载隔离源码；集成检查覆盖paired报告中的逐折/seed预测诊断和训练区间先于验证区间。上文先前记录的953项是较早隔离版本的结果，本次956项为更新后的复跑数。以上仍是隔离原型证据，不是主工作树验收；主工作树`metrics.py`/`diagnostics.py`/`library.py`仍未集成ICIR字段。

本次只更新证据台账，没有改研究源码、活动campaign、协议、快照或历史run。复核时h=10父进程PID 21012和#44 paired worker PID 26896仍存活，worker CPU时间持续增长；其progress文件仍仅显示`started`，尚无完整矩阵或终态评价。冻结源码哈希不变；须等外层多期限campaign终止后，才能把IC/ICIR补丁审阅合入主树并执行主树定向及全量验收。

**h=10候选#44进入paired候选矩阵（2026-09-26 09:23本地时间）**：实际paired `progress.json`显示候选侧A折seed44 LightGBM `model_started`、当前已完成模型0/5；worker PID 26896及父PID 21012仍存活，stderr/stdout与模型工件最近写入09:23。该状态说明候选评估已由启动/数据准备推进到模型矩阵，不是折完成或经济结果；继续保留原run。

**h=10候选#44 seed44继续推进（2026-09-26 09:24本地时间）**：同一paired `progress.json`已推进至D折LightGBM `model_started`，seed44完成模型数3/5；PID 21012/26896仍存活且worker CPU累计增加。当前只是候选侧部分seed矩阵进展，余下折/seed、配对差异、消融和成本压力结论均未完成。

**h=10候选#44配对矩阵完成并进入终评（2026-09-26 09:26本地时间）**：`progress.jsonl`明确记录候选侧seed44 E折模型完成、seed44完成（总seed数3）、候选kind完成；随后ablation从baseline缓存复用，paired阶段进入`evaluating`。父/worker PID 21012/26896仍存活。此证据证明候选模型矩阵完成，但不证明最终evaluation、成本压力/风险门槛或正式结论完成；等待评价文件落盘后再检查完整15折/seed和各门槛。

**h=10候选#44终态与#14启动（2026-09-26 09:27本地时间）**：`alpha101-87ff4877a37efce4dab1ffed-alpha101_044_etf`正式`rejected`，evaluation完整15折×seed；原因`ablation_not_confirmed`、`absolute_risk_limit`、`cost_pressure_return_limit`、`cost_pressure_risk_limit`、`no_majority_fold_increment`、`seed_instability`。逐行计算：候选及2×压力最大回撤均6/15超过12%（最大17.30%、17.35%）；5/15行压力超额收益低于-3%（最低-6.85%）；配对超额收益增量15/15为0，`volume_price`组消融拒绝。evaluation/paired report/manifest SHA256分别为`321ed0a276e6e79d6af84f2093c47ee40a06dcf5d97c7b86af575afb3f2e79c3`、`79c539056784b8a801f527ab3273b9c95e050a54aef2ca9b707b6d749f9ded55`、`4ec782eae33e5ebd44098896c228bea2e964b90606590c71d995fc44d5491621`。外层已登记h=10第2/5候选`alpha101_014_etf`，父进程PID 21012存活，worker已退出；campaign继续原序列，未改变尝试上限或源码。

**h=10候选#14实际分派（2026-09-26 09:30本地时间）**：paired worker PID 26652已启动，run `alpha101-beedd00b37b13edf51a11bcf-alpha101_014_etf`的候选侧seed42已推进到B折LightGBM（完成1/5模型，正在启动2/5）。父PID 21012与worker存活；这不是终态评价，继续监测原campaign。

该paired run随后推进到候选侧seed42 D折LightGBM启动，已完成3/5模型；C折辅助策略缓存命中并完成。父/worker PID 21012/26652仍存活，当前仍是部分候选模型进度，不是终态或可比较结果。

### IC/ICIR方向防泄漏测试补强（2026-09-26）

在隔离副本新增`test_validation_sign_cannot_change_training_selected_orientation`：保持训练段不变、将验证段因子翻向，断言训练方向选择不变且原始验证IC相应翻向；方向不可用时继续保持unknown。该测试单项`1 passed`；更新后完整`tests/unit`为`957 passed, 6 warnings`（92.58秒），是对上文956项结果的增量复跑。此次只新增隔离副本测试并更新本台账，未改冻结主树源码；仍需campaign终态后主树合入与复验。

**h=10候选#14进展（2026-09-26 09:35本地时间）**：paired `progress.json`显示候选侧seed43 E折LightGBM启动、当前该seed已完成4/5模型；PID 21012/26652仍存活。矩阵和最终评价尚未完成。

同一paired运行随后记录候选kind已完成seed42、seed43（2/3），并启动seed44（2026-09-26 09:35:57 UTC）；父PID 21012/worker PID 26652仍存活，尚无`evaluation.json`。当前候选仍在计算，不作经济结论。

### IC/ICIR方向输入校验补强（2026-09-26）

隔离实现中的`select_factor_orientation`现直接校验训练指标：IC/ICIR必须为有限数、ICIR状态必须`available`，且两者同号才选择正向/反向；对缺失、异号、NaN/Inf或无效状态返回`unknown`，不再只信任派生方向标签。指标边界测试11 passed；相关指标/诊断/筛选单测合计50 passed。新增这些边界用例后完整隔离`tests/unit`为`962 passed, 6 warnings`（90.14秒），`tests/integration/test_paired_research.py`为`1 passed, 16 warnings`（27.68秒）。这些仍不是主工作树验收，且没有provider调用或holdout读取。

**IC/ICIR筛选规则manifest复核（2026-09-26）**：检查隔离版`screen_library`后确认，训练内IC/ICIR定向规则已包含于`SCREEN_RULE`，且作为screen identity写入`screening_manifest.json`；报告的逐折信号字段也引用该冻结规则。定向重跑`test_library_screening.py`与`test_predictive_metrics_icir.py`共`33 passed`（16.30秒），验证manifest落盘/复用、negative方向、缺失/常量边界及统计口径。此项此前误判为缺漏，现以实际源码和测试更正；不需要额外字段。仍属隔离副本证据，待正式campaign退出后才合并主树。

**既有筛选manifest冻结复核（2026-09-26 10:33本地时间）**：当前实际shortlist来源`artifacts/library_screening/plan18-alpha101-screen-v2/screening_manifest.json`的冻结`screen_rule.direction`为`published orientation; no sign or window search`，文件SHA256=`CB2DC9F5B7F476503ECA3A44FF4988A843A5B0CBEA414725E3B7CD4E92111588`。因此本轮已运行h=1/5/10/20 campaign不能声称执行了用户随后确认的IC/ICIR训练内反向规则。该manifest和shortlist保持不变；IC/ICIR并入主树后必须新建screen run、重新预注册并生成新shortlist/hash，再决定是否进行后续正式campaign。

**h=10候选#14终态与#3启动（2026-09-26 09:40本地时间）**：`alpha101-beedd00b37b13edf51a11bcf-alpha101_014_etf`正式`rejected`，15折×seed齐全；原因`ablation_not_confirmed`、`absolute_risk_limit`、`cost_pressure_return_limit`、`cost_pressure_risk_limit`、`drawdown_deterioration`、`no_majority_fold_increment`、`seed_instability`。候选及2×压力回撤分别6/15超过12%（最大17.30%、17.35%）；压力超额收益5/15低于-3%（最低-6.85%）；配对增量0正/1负/14零，中位数0；`reversal`组消融拒绝。evaluation/paired report/manifest SHA256分别为`8f39c4ab425d16ec24619123f86c8242067035b98dc22c72ae34de0a25c9a844`、`9e37505efc4d2430141541a86730eda9c1036eef2541bba47527a809d0e3aba6`、`37f36d71d5f685f6cad07889bd30824aa1db8d18d429ded5d94b586520dc927b`。外层父PID 21012随后登记h=10第3/5候选`alpha101_003_etf`（trial_index=2），campaign仍running；冻结源码、协议、快照和尝试上限未变。

**h=10候选#3实际paired进展（2026-09-26 09:43本地时间）**：worker PID 23284已启动；paired run `alpha101-57264a0c74c4b0e667112bb4-alpha101_003_etf`候选侧seed42推进到C折LightGBM（完成2/5模型）。父PID 21012和worker存活，尚未形成完整矩阵或终态评价。

该run随后推进到候选侧seed42 E折LightGBM启动，已完成4/5模型；父/worker PID 21012/23284仍存活，当前没有候选终态评价。

随后候选侧seed42五折完成，paired进度转入seed43（1/3个seed完成）；PID 21012/23284仍存活，研究继续运行。

**h=10候选#3 seed43进展（2026-09-26 09:46本地时间）**：paired `progress.json`显示候选侧seed43 B折LightGBM启动，该seed完成1/5模型；父PID 21012和worker PID 23284仍存活，未产生终态评价。

该paired run随后推进至候选侧seed43 C折LightGBM启动，完成2/5模型；父/worker仍存活，继续等待同一run。

随后paired进度已到候选侧seed43 D折LightGBM启动，完成3/5模型（2026-09-26 09:47 UTC+8）；父PID 21012和worker PID 23284仍存活，h=10 #3仍无正式evaluation终态，h=20尚未初始化。

该run继续推进至候选侧seed43 E折LightGBM启动，完成4/5模型（09:48 UTC+8）；父/worker CPU累计继续增长，尚无终态评价文件。

候选侧seed43五折完成后，paired进度进入seed44 A折LightGBM（0/5模型已完成，09:49 UTC+8）；父/worker PID 21012/23284仍存活，第三候选仍在执行。

**h=10候选#3 seed44进展（2026-09-26 09:50本地时间）**：paired进度显示seed44已推进至B折LightGBM启动、完成1/5模型；父PID 21012和worker PID 23284仍存活，终态evaluation尚未生成。

该run随后推进至候选侧seed44 C折LightGBM启动，完成2/5模型（09:50:41 UTC+8）；父PID 21012/worker PID 23284仍存活，h=10第3项仍无正式终态。

同一candidate矩阵随后到达seed44 E折LightGBM启动，已完成4/5模型（09:51:57 UTC+8）；PID 21012/23284仍存活，正式paired评价尚未完成。

**h=10候选#3进入paired终评（2026-09-26 09:52本地时间）**：`progress.jsonl`确认seed44 E折模型和辅助策略完成，candidate kind三个seed全部完成；随后ablation从baseline缓存复用，paired状态进入`evaluating`。父/worker仍存活；此时尚无正式evaluation，不能判定经济结果。

**h=10候选#3终态与#55启动（2026-09-26 09:54本地时间）**：`alpha101-57264a0c74c4b0e667112bb4-alpha101_003_etf`正式`rejected`，evaluation含15行；原因`ablation_not_confirmed`、`absolute_risk_limit`、`cost_pressure_return_limit`、`cost_pressure_risk_limit`、`no_majority_fold_increment`、`seed_instability`。候选及2×成本压力最大回撤均6/15超过12%（最大17.30%、17.35%）；压力超额收益5/15低于-3%（最低-6.85%）；配对增量15/15为零，`volume_price`消融拒绝。evaluation/paired report/manifest SHA256分别为`6185a24e48e96373ae9308b13a6bbaf49460d877539f5bbc63d9081b1ad2e518`、`a396de968afa51e9374b04af5edfa8abd8e53da01426de4d2c51f02c79b6903a`、`4b4c8b5e38051394152e5afed39a4b44372aa77283f334acf327fe24030bf4b1`。外层随后登记h=10第5/5项`alpha101_055_etf`（trial_index=4）；父PID 21012仍运行，campaign上限与冻结输入未变。

**h=10候选#55实际分派（2026-09-26 09:56本地时间）**：paired worker PID 23532已运行，run `alpha101-51badceaddbce83eee564878-alpha101_055_etf`候选侧seed42开始A折LightGBM（0/5模型完成）。h=10父PID 21012仍存活；这是已声明5次上限的最后一次尝试，不增加或重启campaign。

该paired run随后进入候选侧seed42 C折LightGBM启动，已完成2/5模型；父/worker PID 21012/23532仍存活，最后一次尝试仍在计算。

该run继续推进到候选侧seed42 D折LightGBM启动，已完成3/5模型（09:57:49 UTC+8）；父/worker仍存活，尚未形成结论。

随后候选侧seed42推进到E折LightGBM启动，已完成4/5模型（09:58:25 UTC+8）；父PID 21012与worker PID 23532仍存活。

候选侧seed42五折完成后，paired进度转入seed43 A折LightGBM（09:59:36 UTC+8），seed43当前0/5模型完成；父/worker仍存活。

同一run随后seed43进入B折LightGBM，完成1/5模型（10:00:00 UTC+8）；父/worker仍存活。

该paired运行随后推进至seed43 C折LightGBM，完成2/5模型（10:00:32 UTC+8）；h=10父PID 21012与最后候选worker PID 23532仍存活。

随后seed43进入E折LightGBM，完成4/5模型（10:01:46 UTC+8）；父/worker仍存活，尚无正式evaluation。

候选侧seed43五折完成后，paired进度已启动seed44（seed完成数2/3，10:02:31 UTC+8）；父/worker仍存活。

seed44当前已推进到B折LightGBM，完成1/5模型（10:03:21 UTC+8）；最后候选仍在paired执行阶段。

随后seed44推进至C折LightGBM，完成2/5模型（10:03:53 UTC+8）；父/worker保持存活。

**h=10候选#55 seed44进展（2026-09-26 10:04本地时间）**：paired进度推进到D折LightGBM启动，seed44完成3/5模型；父PID 21012/worker PID 23532仍存活，最后一次尝试尚未终态。

同一run随后进入seed44 E折LightGBM，完成4/5模型（10:05:07 UTC+8）；父/worker继续运行。

**h=10候选#55进入paired终评（2026-09-26 10:05本地时间）**：`progress.jsonl`确认seed44 E折完成、candidate kind三种seed全部完成，ablation从baseline缓存复用，paired进入`evaluating`。父/worker仍存活；等待正式evaluation与campaign账本终结。

**h=10候选#55终态与campaign闭合（2026-09-26 10:07本地时间）**：`alpha101-51badceaddbce83eee564878-alpha101_055_etf`正式`rejected`，完整15折×seed；原因`ablation_not_confirmed`、`absolute_risk_limit`、`cost_pressure_return_limit`、`cost_pressure_risk_limit`、`no_majority_fold_increment`、`seed_instability`。候选及2×成本压力回撤均6/15超过12%（最大17.30%、17.35%）；压力超额收益5/15低于-3%（最低-6.85%）；15/15配对增量为零，`volume_price`消融拒绝。evaluation/paired report/manifest SHA256分别为`ee9fd20d786d4940833709b64986d9e7c12106c49b33e5d1f80e87c8fc22085b`、`2db71f78efb68e47003daeee9e44fc85c97e2a1c4fb00b74f9296f287de8e586`、`a372b5937a55be2ae050af33fbc865d500fe509d847a56809cbec5da78c9418d`。h=10 `formal_evaluation_report.json` status=`completed`、5/5已提交、5/5 rejected、0 failed、0 open、账本15事件有效，`usage_audit_status=completed`；provider dispatch 0、费用`not_applicable_no_provider_dispatch`、usage覆盖`null`，holdout未读、investment readiness=`not_ready`。正式总报告SHA256=`32c278aec7d7cf69cf7f43198038f746d40f6f2b60352d40a49f3c4921dc95a2`。这表示h=10这批未找到accepted因子，不把相对最好者升级为成功。

**h=20正式campaign启动（2026-09-26 10:08本地时间）**：外层父进程PID 21012在h=10完成后创建`plan18-alpha101-horizons-v1-h20`并启动首项`alpha101_040_etf`（trial_index=0）。h=20 protocol ID=`23ce45d1b782a8419eef23229d25d64b61d5faf39bc22235626d4d08dee2572a`，与h=10不同；snapshot仍为`8b384b5d9f8fa10d3d7b6aebbf11fd9463dc76cc9fe203d38dfc6f5ad4322831`、source hash仍为`eac2c70a168241ae2928902bfd3d0e7faacc22a70fcabe7a6a96b2055f6b8c75`，campaign上限5、external calls关闭、holdout未读。四期限campaign尚未结束。

**h=20候选#40 paired worker分派（2026-09-26 10:09本地时间）**：worker PID 13144已执行run `alpha101-2f26144c30cd8dbc7c7e53d8-alpha101_040_etf`的paired请求，外层父PID 21012仍存活；启动阶段尚无配对矩阵或候选结论。

该paired已进入baseline侧A折seed42 LightGBM（0/5模型，10:10:25 UTC+8）；worker PID 13144 CPU累计增加、进程仍活跃。

**IC/ICIR方向口径确认（2026-09-26）**：按用户补充，训练期IC<0且ICIR<0时将该因子视为反向信号候选，训练期选择反向（方向系数-1）；验证/测试期仅按已冻结方向计算原始与方向统一后的诊断，不得用验证IC/ICIR重新选方向。原始符号仍须保留在逐折/seed报告中；该口径不单独构成正式因子通过结论，跨折仍只逐折汇报。该定义与计划文档既有约定一致，隔离原型已覆盖方向泄漏和输入边界，主树合并仍等待campaign终态。

**IC/ICIR隔离原型回归复核（2026-09-26 10:54本地时间）**：隔离副本的 `test_predictive_metrics_icir.py` 与 `test_library_screening.py` 共`33 passed`（15.59秒）；paired正式集成测试`test_paired_research.py`为`1 passed, 16 warnings`（28.16秒）。随后完整`tests/unit`复跑为`962 passed, 6 warnings`（92.71秒）。覆盖负IC/ICIR训练期反向定向、原始/反向验证指标并列、unknown边界、screen manifest冻结方向规则，以及paired逐折/seed报告字段。以上仅验证 `%TEMP%\etf-qlib-plan18-icir-20260926`，不代表主工作树已集成；horizon campaign父进程PID 21012与paired worker PID 13144仍存活且CPU时间增长，h=20进度仍为`candidate_started`，故未改活动源码/协议/工件，也未读取holdout或调用provider。

**h=20候选#40基线侧推进（2026-09-26 10:11本地时间）**：paired `progress.json`从成本压力辅助回测推进到baseline侧A折seed42的`equal_weight_pool`辅助回测；paired worker PID 13144与外层父PID 21012仍存活。因子候选模型矩阵及经济评价尚无终态，不作结论。

该baseline侧A折seed42的`equal_weight_pool`基础辅助回测于10:13:22完成，并立即开始同策略2倍成本压力辅助回测；paired worker PID 13144和父PID 21012持续存活。当前仍在baseline辅助诊断阶段，尚未开始候选模型矩阵或产生正式判断。

**h=20候选#40 paired矩阵启动（2026-09-26 10:15本地时间）**：baseline侧A折seed42的`equal_weight_pool`基础与2倍成本压力辅助回测均已完成，baseline模型矩阵现推进至B折seed42（已完成1/5折模型）；paired worker PID 13144和父PID 21012持续存活。当前是baseline侧部分模型进展，不构成候选结论。

**h=20候选#40 baseline推进（2026-09-26 10:16本地时间）**：baseline侧B折seed42的`manual_momentum`基础辅助回测完成并开始2倍成本压力回测；worker PID 13144 CPU累计增加至约393.5秒，父PID 21012仍存活。当前仍未完成baseline全矩阵，更未开始候选侧结论。

随后B折seed42的`manual_momentum`压力回测完成，`equal_weight_pool`辅助策略开始（10:16:30）；h=20 paired与外层父进程仍活跃。主树`code_hash()`复核仍为冻结campaign声明的`eac2c70a168241ae2928902bfd3d0e7faacc22a70fcabe7a6a96b2055f6b8c75`，无源码身份漂移。

**h=20候选#40 baseline B折辅助回测推进（2026-09-26 10:19本地时间）**：`equal_weight_pool`基础辅助回测完成并开始2倍成本压力回测；paired worker PID 13144与campaign父PID 21012均存活且CPU时间增长。baseline和candidate主矩阵/终审仍未闭合。

随后`equal_weight_pool`的2倍成本压力辅助回测完成，baseline侧B折seed42矩阵已完成（2/5折）；paired推进至C折seed42 LightGBM（10:21:44 UTC，10:21本地），worker PID 13144与父PID 21012仍存活。候选侧矩阵尚未开始。

**h=20候选#40 baseline C折进展（2026-09-26 10:22本地时间）**：baseline侧seed42 C折LightGBM已完成（模型矩阵3/5），其`manual_momentum`基础辅助回测完成并开始2倍成本压力回测。worker PID 13144与父PID 21012仍存活；候选侧矩阵和正式结论尚未开始。

随后C折`manual_momentum`压力辅助回测完成，paired进入`equal_weight_pool`基础辅助回测（10:22:41 UTC，10:22本地）。worker PID 13144与父PID 21012仍活跃，主campaign尚无候选终态。

**h=20候选#40 C折辅助回测推进（2026-09-26 10:25本地时间）**：baseline侧`equal_weight_pool`基础辅助回测完成，2倍成本压力辅助回测已启动（10:25:32）；worker PID 13144、campaign父PID 21012仍存活。A/B/C模型进度保持3/5，候选侧未开始。

随后C折`equal_weight_pool`压力辅助回测及辅助策略完成，baseline侧seed42矩阵推进到D折LightGBM启动（10:28:18 UTC，10:28本地），当前3/5折模型完成。worker与父进程仍活跃，尚无候选侧或正式evaluation。

**h=20候选#40 baseline D折进展（2026-09-26 10:29本地时间）**：baseline侧seed42 D折LightGBM已完成，矩阵为4/5；D折`manual_momentum`基础与2倍压力辅助回测均完成，当前执行`equal_weight_pool`基础辅助回测。worker PID 13144、父PID 21012仍存活，候选侧矩阵尚未开始。

随后D折`equal_weight_pool`基础辅助回测完成并开始2倍成本压力辅助回测（10:32:02）；baseline seed42仍为4/5折模型完成，worker PID 13144和campaign父进程PID 21012持续存活。

**h=20候选#40 baseline seed42完成（2026-09-26 10:36本地时间）**：D折`equal_weight_pool`2倍成本压力辅助回测完成，baseline D折闭合；E折LightGBM完成后启动辅助回测。baseline seed42模型矩阵5/5完成，当前正在跑E折`equal_weight_pool`基础辅助回测；paired worker PID 13144与父PID 21012仍活跃，baseline其它seed/候选仍未结束。

**h=20候选#40 baseline E折辅助阶段（2026-09-26 10:40本地时间）**：E折`equal_weight_pool`基础辅助回测已完成，2倍成本压力辅助回测启动；模型训练虽为5/5，但E折总体辅助评估未闭合。worker PID 13144与campaign父PID 21012持续存活。

**h=20候选#40 baseline seed42闭合（2026-09-26 10:43本地时间）**：E折`equal_weight_pool`基础/压力辅助评估完成，baseline seed42五折与辅助评估完整闭合；paired已启动baseline seed43（seed 42/3 completed），外层campaign和paired worker仍存活。候选侧矩阵与formal evaluation尚未开始。

**h=20候选#40 baseline seed43推进（2026-09-26 10:44本地时间）**：A折LightGBM与`manual_momentum`、`equal_weight_pool`辅助策略均完成，两个辅助项缓存命中；paired已启动B折LightGBM（baseline seed43当前1/5折），worker PID 13144及父PID 21012仍活跃。

随后baseline seed43 B折LightGBM完成，两项辅助策略均缓存命中并闭合；paired推进至C折LightGBM（seed43完成2/5折，10:44:35 UTC）。h=20 #40候选侧仍未开始，evaluation文件不存在。

**h=20候选#40 baseline seed43继续推进（2026-09-26 10:45本地时间）**：C折LightGBM及`manual_momentum`、`equal_weight_pool`两项缓存辅助均完成；D折LightGBM已完成，当前D折辅助评估进行中（seed43模型4/5完成）。worker PID 13144与父PID 21012仍存活，正式evaluation尚不存在。

**h=20候选#40 baseline seed43闭合（2026-09-26 10:46本地时间）**：E折LightGBM与两项缓存辅助均完成，baseline seed43五折及辅助评估闭合；paired已启动baseline seed44（baseline 3个seed中已完成2个）。候选侧未启动、正式evaluation尚不存在，父/worker进程仍活跃。

**h=20候选#40 baseline seed44推进（2026-09-26 10:47本地时间）**：A折LightGBM完成，两项辅助策略均缓存命中且折已闭合；paired已启动B折LightGBM（seed44当前1/5折）。worker PID 13144和campaign父PID 21012仍存活。

随后baseline seed44 B折LightGBM及两项缓存辅助完成，paired推进至C折LightGBM（seed44当前2/5折，10:47:59 UTC）；候选侧尚未开始。

随后baseline seed44 C折LightGBM与两项缓存辅助均完成并命中缓存，paired已启动D折LightGBM（seed44当前3/5折，10:48:34 UTC）；父/worker继续存活，仍处于baseline阶段。

**h=20候选#40 baseline全矩阵闭合、candidate启动（2026-09-26 10:50本地时间）**：baseline seed44 E折LightGBM与两项缓存辅助命中并完成，seed44及baseline kind三seed均闭合；paired已启动candidate seed42（candidate completed_seeds=0/3）。候选模型矩阵刚开始，尚无折/seed结果或formal evaluation；worker PID 13144与父PID 21012仍存活。

**h=20候选#40 candidate矩阵与paired评价进展（2026-09-26 11:00本地时间）**：同一paired run内部进度文件显示candidate seed42/43/44的A–E折LightGBM及相应辅助策略均闭合，`candidate_report.json`、`baseline_report.json`和`ablation_report.json`各含15行；一次因子消融按协议复用baseline。paired内部状态进入`evaluating`，尚无`evaluation.json`及终态状态，不能判断接受/拒绝。campaign父进程PID 21012与worker PID 13144均仍存活、CPU时间增长；顶层campaign `progress.json`仍停在`candidate_started`，但paired内部`progress.jsonl`已记录到11:00，故以内部进度文件为准，不误判为停滞。源码hash/协议/冻结snapshot不变，未读取holdout、未发起provider调用。

**h=20候选#40正式配对终态（2026-09-26 11:02本地时间）**：`evaluation.json`状态为`rejected`，工程执行完成，无`failed`；原因码为`ablation_not_confirmed`、`absolute_risk_limit`、`cost_pressure_return_limit`、`cost_pressure_risk_limit`、`drawdown_deterioration`、`no_majority_fold_increment`、`seed_instability`。收益增量中位数为0，15行仅1正、2负、12零，变化仅出现在B折，三seed方向混合；9/15行候选回撤超过12%，baseline同样9/15行超限（max drawdown均达17.61%），属于继承风险但正式绝对风险门仍失败；另外有2行候选回撤增量为正，最大+0.00471。2倍成本压力下3/15行超出用户-3%收益线，最差超额收益-7.13%，压力最大回撤17.70%。B折三seed换手均下降，未见换手恶化；但收益、消融、风险和稳定性不足，不能因换手下降接受。事件工件仅结构有效，`source_completeness_verified=false`；未读holdout、provider调用为0。screen-v2仍是published orientation/no sign search，故该结论不包含IC/ICIR训练内反向定向。该项闭合后campaign父进程PID 21012仍运行并已启动第2/5项`alpha101_044_etf`（paired worker PID 27764）；保留其余三次名额及当前所有冻结输入。

**h=20候选#44进度及监控验证（2026-09-26 11:04本地时间）**：paired run `alpha101-ba56b98b3fc00c6243bc88bc-alpha101_044_etf`状态仍为`running`；candidate seed42已完成A/B两折并启动C折LightGBM（2/5模型），进程父PID 21012、worker PID 27764均存活且CPU时间增加。顶层campaign progress仍仅显示`candidate_started`，但只读执行`monitor-run --path artifacts/library_evaluations/plan18-alpha101-horizons-v1-h20 --once`可递归返回paired及worker子目录进度，包括#40终态和#44当前折；因此无需运行时改码。#44尚无evaluation/终态；继续冻结源码与协议。

**W08合成holdout集成验收（2026-09-26）**：在隔离副本以 `PYTHONPATH=src python -m pytest tests/integration/test_holdout_pipeline.py -q`复跑，`2 passed in 352.06s`。这两种合成验收情景覆盖预先冻结模型后的一次性holdout流程、技术失败重试、决策提交中断恢复时复用已完成数据、同一版本/区间不可被其他候选重复消费及holdout报告校验。测试以合成数据夹具在临时目录运行；不是正式snapshot/真实holdout访问，不改变W08真实独立资格未确认状态，也不授权/构成实际投资结论。

**h=20候选#44继续进度（2026-09-26 11:13本地时间）**：paired progress ledger已记candidate seed42、seed43闭合，seed44启动；随后seed44 C折完成并开始D折LightGBM（A–C折完成，D折模型3/5）。父PID 21012与worker PID 27764仍存活；campaign状态仍running。正式evaluation尚未产生，未读取正式holdout；保留其余三个候选名额、源hash及协议。

随后seed44 D折LightGBM及两项缓存辅助策略闭合，E折LightGBM已启动（A–D折完成，E折模型4/5，2026-09-26 11:14本地时间）。seed42/43仍为已闭合状态；#44的candidate_report/evaluation尚未生成，父PID 21012与worker PID 27764继续运行。该进展不改变#44的正式判定状态（尚无），也不改变IC/ICIR新规则未纳入本campaign的边界。

**h=20候选#44模型矩阵闭合、paired汇总中（2026-09-26 11:15本地时间）**：candidate seed42/43/44全部完成，`candidate_report.json`、baseline与ablation各为15个折/seed行；paired进度为`evaluating`，run `status.json`仍running，尚无`evaluation.json`/正式判定。worker与campaign父进程仍运行；不能将模型矩阵完成等同于候选evaluation完成。继续保护冻结source hash、snapshot、protocol和后续名额。

**h=20候选#44正式配对终态（2026-09-26 11:16本地时间）**：paired `evaluation.json`为`rejected`，15行，原因码`ablation_not_confirmed`、`absolute_risk_limit`、`cost_pressure_return_limit`、`cost_pressure_risk_limit`、`drawdown_deterioration`、`no_majority_fold_increment`、`seed_instability`、`turnover_deterioration`。收益增量仅在B折有变化：1正、2负，其余12为零，中位数0；group ablation亦rejected。候选与baseline最大回撤均为17.61%，各有9/15行超过12%绝对线；另有2行候选回撤增加，最大+0.00402。2倍成本下3/15行超额收益低于-3%压力线，最差-6.92%，最大压力回撤17.70%。B折换手增量1正/2负，虽两seed下降仍触发`turnover_deterioration`；不能以少数折的局部增益抵消。该run正式完成、非工程失败，无候选接受；screen-v2仍为published orientation/no sign search，因此不含新IC/ICIR反向规则。终态后campaign父进程PID 21012仍运行，进度已启动第3/5项`alpha101_014_etf`；本campaign未读正式holdout，保留剩余两次尝试与冻结输入。

**h=20候选#14初始paired进度（2026-09-26 11:19本地时间）**：父PID 21012及worker PID 28480均存活；paired `alpha101-42f9ad4529c21bb33c222b5b-alpha101_014_etf`已启动，candidate seed42 A折模型与辅助策略闭合，B折LightGBM已开始。仍为第3/5项，无evaluation/结论；未读取holdout。

**h=20候选#14 paired进度更新（2026-09-26 11:21本地时间）**：只读核验campaign父PID 21012、paired worker PID 28480仍在运行；candidate seed42 A–D折已完成，E折LightGBM已启动（模型4/5），paired run `alpha101-42f9ad4529c21bb33c222b5b-alpha101_014_etf`内部事件比顶层progress更新。当前仍为第3/5候选、尚无正式evaluation；保持snapshot/source/protocol冻结，不读holdout、不做provider调用。用户再次确认负方向口径：训练段IC和ICIR同为负时作为反向信号候选，按训练内方向选择乘以-1；验证段只报告raw与oriented诊断，不以验证数据择向。该规则已在规划及隔离原型中记录，不追溯应用于冻结的screen-v2或本campaign。

**h=20候选#14 paired运行复核（2026-09-26 11:24本地时间）**：通过进程表确认campaign父PID 21012（CPU时间6137.6秒）和worker PID 28480（CPU时间342.4秒）均仍存活且Responding；`monitor-run --once`递归事件显示candidate seed42 A–D已完成、E折在运行，seed43 A/B已完成并已启动C折LightGBM。paired状态仍running，无`evaluation.json`；该次复核没有运行新研究，也没有访问正式holdout或调用provider。W04方向选择实现与测试继续保持在隔离原型中，需等冻结campaign终态后再合入主树并用新screen/run身份验证。

**W07/W08只读研究审计（2026-09-26 11:26本地时间）**：对已终止R05执行`python -m etf_ml.cli audit-research --run-id plan18-r05-w07-audit-20260926 --source-run artifacts/runs/tushare-formal-first-loop-20260925-r05`，审计状态`completed`、source code hash `eac2c70a168241ae2928902bfd3d0e7faacc22a70fcabe7a6a96b2055f6b8c75`、`risk_pairs=15`、external calls/training runs/holdout value reads均为0；保护的snapshot、baseline、R05 source artifacts核验未变。风险归因`partial`，不是全闭合。对`formal-five-20260924`账本保留5/5尝试，仍有2个unresolved usage slots、10个unknown usage attempts和10个unknown-cost calls，usage audit `partial`。两个open attempts具体为r02/r04；对应状态文件均terminal `failed`、checkpoint均`paused_budget`，进程扫描无匹配PID、`uncertain_calls=0`，故判为terminal uncommitted attempts并保留名额，不重发。holdout资格为`ineligible`（`holdout_independence_not_declared`），data qualification为`unknown`（source completeness证据缺失），候选仍`rejected`，投资准备`not_ready`；未读holdout行情值。审计报告SHA256 `706bd3c8ee596344259f72f15f9dc3fa51b24d97660a07c7cf8f6dc2f987761d`，审计目录`artifacts/research_audits/plan18-r05-w07-audit-20260926`。

**h=20候选#14继续运行复核（2026-09-26 11:27本地时间）**：paired事件推进到candidate seed44 C折闭合、D折LightGBM运行（3/5模型）；campaign父PID 21012和worker PID 28480仍Responding，CPU时间分别约6193.9秒和607.7秒。paired仍无`evaluation.json`；该轮继续仅做只读监控。

**h=20候选#14正式paired终态（2026-09-26 11:29本地时间）**：run `alpha101-42f9ad4529c21bb33c222b5b-alpha101_014_etf` status completed、formal `evaluation.json`为`rejected`，15个折/seed配对，原因`ablation_not_confirmed`、`absolute_risk_limit`、`cost_pressure_return_limit`、`cost_pressure_risk_limit`、`no_majority_fold_increment`、`seed_instability`。候选-基线超额收益、最大回撤与换手差值均15/15全为0，中位收益增量0；按判定器规则，各折和每个seed的中位增量均不大于0，故无多数折增益且seed稳定性失败。`group_ablation.json`的`reversal`组移除`alpha101_014_etf_v1`后15行收益增量均0，状态rejected（`group_ablation_not_confirmed`）。候选和baseline各9/15行最大回撤超过12%，两者最大值均17.61%，回撤未因候选恶化但绝对风险线仍失败；2倍成本下候选3/15行累计超额收益低于-3%，最差-6.54%，压力最大回撤17.70%，9/15行超过12%。换手15行完全不变，没有`turnover_deterioration`。15行事件数据`source_completeness_verified=false`；本轮screen-v2没有因子ICIR与反向择向字段，虽旧diagnostics保存原始因子逐日IC/RankIC，但不据此追溯补算/改判。未读holdout，provider调用0。evaluation SHA256 `4408e6b2bdc6510f32b0fb671326dcaec858104f9dc0d902353dee0ff95e49b5`；group ablation SHA256 `011ed79f510cc33158756694ca5925d43c1e3f42db64bc8ba07d756383d02c56`。终态后campaign父PID 21012继续运行并启动第4/5项`alpha101_003_etf`，paired worker PID 3196于11:31启动；随后`monitor-run --once`显示seed43 D折LightGBM运行（3/5模型），campaign仍running，保留最后一次尝试与冻结输入。

**h=20候选#3继续进度（2026-09-26 11:38本地时间）**：只读`monitor-run --once`发现candidate seed43 E折及seed43全部五折闭合，worker随即启动seed44；PID 3196与campaign父PID 21012仍存活、Responding，CPU时间上升。campaign尚在第4/5项，未出正式结论。

随后仅30秒内，candidate seed44 A折模型及辅助策略闭合、B折LightGBM已启动（2026-09-26 11:39本地）；worker PID 3196与父PID 21012仍Responding，配对任务持续推进。

随后candidate seed44 B、C、D折均完成，E折LightGBM启动（模型4/5；2026-09-26 11:41本地）；paired worker PID 3196 CPU时间增至635.4秒，父PID 21012增至6426.8秒，均仍Responding。#3仍无evaluation/terminal status，继续保护活动run。

**h=20候选#3正式paired终态（2026-09-26 11:43本地时间）**：run `alpha101-3f0318e8ae1f16b995e8daf5-alpha101_003_etf`已完成，正式`evaluation.json`为`rejected`，15个折/seed，原因`ablation_not_confirmed`、`absolute_risk_limit`、`cost_pressure_return_limit`、`cost_pressure_risk_limit`、`no_majority_fold_increment`、`seed_instability`。候选-基线超额收益、最大回撤、换手变化15/15行全部为0；中位增量0。组消融`group_ablation.json`为rejected（`group_ablation_not_confirmed`），删去因子对组合结果无影响。候选和baseline各9/15行回撤超过12%，最大均17.61%；2倍成本下3/15行低于-3%，最差-6.54%，压力最大回撤17.70%，其中9/15行超过12%；无换手恶化。15/15行事件`source_completeness_verified=false`，screen-v2不含新ICIR方向规则，未反向择向或追溯改判；未读holdout、provider调用0。evaluation SHA256 `54ab5c0928a2edb29bcf715b8053b4c9aa2a8a0b933548894fa521e052e3c55f`。该项闭合后外层campaign启动最后第5项`alpha101_055_etf`，顶层事件时间11:43:35本地；campaign父PID 21012仍存活，尚无campaign终态。

**h=20最后候选#55启动（2026-09-26 11:45本地时间）**：paired run `alpha101-68eb78d3398c5950df3c85aa-alpha101_055_etf`已创建；worker PID 23284、campaign父PID 21012均存活，seed42 A折LightGBM启动（0/5模型完成）。campaign第5/5项已正式进入paired阶段；尚无候选结论。

随后seed42 A、B折及对应缓存辅助策略闭合，C折LightGBM运行中（2/5模型完成；2026-09-26 11:46本地）；worker PID 23284 CPU时间升至141.6秒、父PID 21012升至6549.8秒，两进程均Responding。

随后seed42 C、D折闭合并启动E折LightGBM（4/5模型完成；2026-09-26 11:47本地）；worker PID 23284 CPU时间193.9秒、父PID 21012为6560.6秒，仍在运行。

随后candidate seed42 E折及其辅助评估完成，seed42矩阵闭合；已启动seed43（2026-09-26 11:48本地）。当前#55无evaluation，worker PID 23284与父PID 21012仍活跃，正式终态未产生。

2026-09-26 11:49本地复核：seed43 A折LightGBM已启动；paired worker PID 23284（CPU 260.9秒）与父PID 21012（CPU 6575.0秒）仍存活且Responding。campaign状态仍为第5/5项running。

随后candidate seed43 A折模型与辅助策略闭合，B折LightGBM启动（1/5模型完成；2026-09-26 11:49本地）；worker CPU时间增至316.1秒、父进程6586.1秒，均仍活跃。

随后seed43 B、C折及辅助策略闭合，D折LightGBM运行中（3/5模型完成；2026-09-26 11:50本地）；worker PID 23284与campaign父PID 21012的CPU时间继续增长，仍未生成候选评价。

随后seed43 D折闭合、E折LightGBM启动（4/5模型完成；2026-09-26 11:51本地）；两个进程CPU继续增长，paired仍running。

随后seed43 E折与该seed矩阵闭合，paired启动candidate seed44（2026-09-26 11:52本地）；A折LightGBM刚开始，worker PID 23284、父PID 21012仍Responding，尚无formal evaluation。

随后seed44 A–C折闭合，D折LightGBM运行中（3/5模型完成；2026-09-26 11:54本地）；worker PID 23284 CPU 561.7秒、父PID 21012 CPU 6636.9秒，仍在推进。

随后seed44 D折闭合并启动E折LightGBM（4/5模型完成；2026-09-26 11:55本地）；两进程继续活跃，candidate seed44仍待收尾。

随后candidate seed44 E折及该seed矩阵闭合，候选15行矩阵与复用baseline ablation阶段全部结束，paired进入`evaluating`（2026-09-26 11:55本地）；正式`evaluation.json`尚未落盘，不能提前报告结论。

### IC/ICIR折级硬门槛原型与回归（2026-09-26）

结合用户确认的硬门槛`IC>0且ICIR>0`及“IC、ICIR均小于0则作为反向指标”的方向要求，已将实现合入主树。训练分段中IC与ICIR同为正时冻结sign=+1，同为负时冻结sign=-1；不一致、零值、缺失、样本不足、零方差或非有限值均为`unknown`。逐折验证并列报告原始与方向化IC/ICIR；方向化验证IC、ICIR均严格大于0时该折门槛`passed`，任一非零有效指标不为正则`failed`，零/不可计算为`unknown`。paired因子与模型预测按折/seed报告，library筛选保存方向、训练窗口及逐折状态；screen rule纳入run identity。没有跨折汇总或候选级IC/ICIR通过规则，不能替代正式组合门槛或推导候选accepted。旧screen-v2仍按其冻结规则解释，不能追溯改判；新方向规则须用新run identity生效。

主树验证：IC/ICIR与library/diagnostics定向回归`37 passed, 16 warnings`；完整`tests/unit`复跑`963 passed, 6 warnings`（89.48秒）；规则版本升至library-screen-v2后的预测指标/library筛选回归`35 passed`。此前首轮焦点回归`46 passed, 16 warnings`。唯一一次全量失败来自旧测试假设训练数据变动不得影响任何方向诊断；已按新规则改为断言训练常量化时orientation退化为unknown、外层原始验证统计不变，并在全量复跑通过。paired Qlib真实集成用例包含在37项定向回归中，核验ICIR字段与折级方向门槛形状。主树代码哈希：`cec5031ede94284e99bd952767672d9664e88080d32de87f7b2bdeabd620eee3`。该验证仅证明实现及固定样例，不会追溯更新既有campaign，也未发起provider调用、holdout读取或新正式campaign。

### W05多期限Alpha101 campaign终态补记（2026-09-26）

`plan18-alpha101-horizons-v1`四期限均终态完成，每期限5项正式paired评估均`rejected`，工程失败0；共20项正式候选，没有accepted。h=20最后候选`alpha101_055_etf`的拒绝理由为`ablation_not_confirmed`、`absolute_risk_limit`、`cost_pressure_return_limit`、`cost_pressure_risk_limit`、`no_majority_fold_increment`、`seed_instability`。15折/seed中候选相对baseline的超额收益、回撤、换手差值均为0；组消融无影响。候选与baseline各9/15行最大回撤超过12%；2倍成本下3/15行超额收益低于-3%，最差-6.5396%，压力最大回撤17.7029%。数据事件source completeness仍不完整；provider调用0，未读取holdout。screen-v2冻结为published orientation/no sign search；本段IC/ICIR方向实现不回写这20项结果。该campaign否定了本轮Alpha101 shortlist的正式合格性，不代表预筛系统或成熟库已找到通过因子。

### W04训练内方向规则正式离线筛选（2026-09-26）

在不修改旧run的前提下，对同一冻结snapshot `8b384b5d9f8fa10d3d7b6aebbf11fd9463dc76cc9fe203d38dfc6f5ad4322831`和原20个定义，以新身份`plan18-alpha101-screen-v3-icir`重跑`screen-library`。筛选manifest的规则版本为`library-screen-v2`，screen运行源码hash `3908f17219747ac89598102d3a85c553cc7ae09bb20d5df2d0f51475c958f02c`；snapshot manifest hash与冻结值`61337b65db6ed2f37a33b4cdca93a8596d3e2f9bbcb4193f03c3aff20f17d8e5`一致。完整20项：技术通过17、因覆盖不足未评分3；8项预筛拒绝、4项达到入围条件但超5项上限、shortlist 5项。85条已评分折记录中，训练方向62条为正、23条为反向；方向化验证IC/ICIR状态53条passed、32条failed。Shortlist仍为`alpha101_040_etf`、`alpha101_044_etf`、`alpha101_014_etf`、`alpha101_003_etf`、`alpha101_055_etf`；#55在5折训练均识别为反向，但方向化验证仅2折passed、3折failed。因用户暂未规定跨折聚合，本报告不将折级状态聚成候选级IC/ICIR结论；shortlist不是接受结论。该批为0 provider调用、0正式evaluation、holdout_evaluated=false。报告SHA256 `51b3cbd0a4dfa300691fbbf4d1c6aa45949b15c20e628984888d80d067672c82`；shortlist SHA256 `f94da9976cedf57a977eca7cb4b7f8308eed96c7adb39f92f8c874ecf36a304e`；RunStore files通过`verify_files`校验。结果与旧screen shortlist及已有5项正式拒绝清单相同；不重启付费formal campaign，也不将旧评价伪装成新run的评价。

本次运行在冗余诊断的常量横截面相关性上产生NumPy除零告警。已在相关计算前检测横截面秩方差并跳过不可定义的相关性；零方差仍作为unavailable，不填0。新增回归验证该路径不发warning，并新增负训练方向经sign=-1后通过验证门槛的独立小样例；`test_library_screening.py`与ICIR新测试共`37 passed`。当前源码hash（包含此告警修复）为`576a4b674942391e8f894be441b167ed3b33d032993ed86e1d250975f4e834e0`；离线screen输出保留其运行时hash与证据，不回写覆盖。

最终回归：当前工作树`tests/unit`全量`965 passed, 6 warnings`（89.13秒）；`tests/integration/test_paired_research.py`为`1 passed, 16 warnings`（27.87秒）。warnings来自已知Pandas dtype降级、MLflow filesystem backend提示与Qlib空切片，不涉及ICIR门槛失败。`git diff --check`通过；W04离线筛选仍明确是screening而非formal/holdout acceptance。

### RDAgent集成反馈中IC/ICIR状态投影回归（2026-09-26）

继续执行完整`tests/integration`：首次结果`100 passed, 1 failed`（499.22秒）。唯一失败是集成测试将内部逐日诊断（含日期序列）与发给研究反馈的脱敏摘要作全对象相等比较；摘要按安全投影不应携带`by_date`，且此前未投影新增的IC/ICIR方向与可用状态字段。已在`compact_diagnostics`中明确纳入IC/ICIR与Rank IC/Rank ICIR方向、可用状态，同时继续排除逐日序列；将集成断言改为与同一安全投影比较。定向复验`tests/unit/test_development_feedback.py tests/unit/test_predictive_metrics_icir.py`为`32 passed`；原失败用例`test_real_rdagent_adapters_cli_multitrial_and_safe_feedback`单项重跑为`1 passed in 47.02s`。因此本次integration全套的101个用例均有绿色结果（100项首跑通过，失败项修复后复验通过；没有在修复后重复运行整套）。当前代码hash `13cffb489a1b5636c09271fb66589f91116efe9625251b3b0ed8ada593e61bb3`；`git diff --check`通过。未调用付费provider、未读取真实holdout、未启动正式campaign。该变更只影响诊断摘要字段投影，不改IC/ICIR方向门槛、验证择向规则、筛选结果或既有campaign结论。

### W07/W08前一轮源码复核（2026-09-26）

为避免引用旧代码版本的审计结论，以当前源码hash `576a4b674942391e8f894be441b167ed3b33d032993ed86e1d250975f4e834e0`重新只读审计R05，run `plan18-r05-w07-audit-20260926-r2`。审计`completed`，风险配对15行；external calls、training runs、holdout values read均0；snapshot、baseline与source run保护文件均unchanged。审计report SHA256 `dfa893061ea8caed5cfc098721085dfce04f7afd91377b7de425ccf5562ed153`，风险报告SHA256 `b749a37024903cabfe384fa331168f1d85ee61bedc226503f3620ab13c180239`，与上一版本审计计算值一致。campaign `formal-five-20260924`仍`usage_audit_status=partial`，原因`unresolved_trial_usage_slots`、`provider_cost_unknown`、`provider_usage_incomplete`；未结usage slots 2、usage unknown attempts 10、unknown-cost calls 10。r02/r04均为terminal failed / `paused_budget`、没有匹配存活进程、`uncertain_calls=0`，名额继续保留。真实provider receipt未提供，未把unknown改成零或completed。

同次资格报告仍为data qualification `unknown`（事件和调整源完整性证据缺失）、factor evaluation `rejected`、holdout qualification `ineligible`（`holdout_independence_not_declared`）、holdout evaluation `not_run`、investment readiness `not_ready`；无访问holdout行情值。W07/W08因此是外部usage凭证和独立资格/候选门槛未满足，不是本轮审计代码失败。审计详情位于`artifacts/research_audits/plan18-r05-w07-audit-20260926-r2`。

### W07/W08此前源码复核（2026-09-26）

因RDAgent反馈投影和风险时序诊断随后有代码变更，r3已被下节的r4审计取代。r3只作为此前源码身份下的只读历史记录保留；不得将其hash解释为本次最新源码审计。

### A03-A05 同日开盘风险判断/执行时序显式归因（2026-09-26）

回测风险检查现在在`risk_checks.json`明示`decision_valuation_basis`、`execution_reference_price_basis`和`decision_execution_timing_status`。现有行为按代码路径记为当日开盘估值、当日开盘参考价及`same_session_open_ordering_unverified`；非检查日显式为`not_checked`。`paired_daily_risk_attribution`把这些字段带入逐日/风险事件报告，旧工件缺字段时保留`unknown_legacy_trace`。这只增加来源可追溯的时序事实，不改变下单、成交、风险阈值、旧协议或旧工件，也不声称同开盘时间先后已被证明。非调仓日尚未执行每日风险检查；最早合法退出时点仍无法由只有日频成交状态的旧账本可靠推导。

验证：`tests/unit/test_daily_risk_attribution.py`为`7 passed`；固定Docker/Qlib配对集成`tests/integration/test_paired_research.py`为`1 passed, 16 warnings in 25.19s`，验证实际生成风险检查到配对诊断的时序字段传递。Docker为Linux 29.5.2，镜像digest已存在。全量`tests/unit`为`966 passed, 6 warnings in 91.88s`。未运行新策略/正式campaign、无provider调用、未读holdout。该证据改善A03/A05的可观测性，但没有关闭A03–A05的每日风险策略、真实下单先后和最早合法退出验证。

### W07/W08当前源码复核（2026-09-26）

在本轮风险时序诊断代码改动后，再以当前源码hash `878a5df5a57885a454108ae2999e8c783771c41a04f3cc93bebd9018ff7ff995`只读审计，run `plan18-r05-w07-audit-20260926-r4`。审计completed、风险配对15行；external calls/training runs/holdout value reads均0；snapshot、baseline、source run protected files全部unchanged。audit报告SHA256 `2e4e58f6e9443bcc9a098c765f458c0c790614970ae82feabc8cfb7c9056899e`；qualification报告SHA256 `603e17f492f2d69b870d5d081a1174051306388954b44c02a1c12383d4419a33`；风险报告SHA256 `748ce58b35eacd4e87865f3193c8ff31fd5790ccf091d3c91fc829448c5cb37c`。campaign `formal-five-20260924`仍usage partial：5/5尝试、2个未结usage slots、10个token未知attempts、10个unknown-cost calls；真实供应商凭证缺失，r02/r04名额继续保留。资格仍为data unknown、factor rejected、holdout ineligible（`holdout_independence_not_declared`）、holdout not_run、prospective not_started、investment not_ready；未读取holdout行情。工件位于`artifacts/research_audits/plan18-r05-w07-audit-20260926-r4`。

### A19 计算缓存环境身份失效测试（2026-09-26，阶段性证据）

`materialize_library_factor`的cache key已包含`environment_manifest()`；新增合成回归，在输入面板、定义和缓存路径保持不变但环境清单变化时，断言不命中旧缓存且生成新cache key。`tests/unit/test_library_screening.py`全文件`24 passed in 14.60s`，其中定向cache身份两项`2 passed in 1.41s`。这仅记录最初关闭“因子计算缓存对环境身份变化失效”子场景；正式评价缓存和同条件计时随后由下方A19正式评价缓存身份回归补齐。无provider调用、无holdout读取、未修改历史缓存或运行工件。

当前工作树回归：`E:\tools\anaconda\envs\qlib_zhengshi\python.exe -m pytest tests/unit -q`结果`966 passed, 6 warnings in 90.39s`，退出码0；6项为既有Pandas FutureWarning（补充数据拼接与universe状态下采样），无测试失败。源码hash仍为`13cffb489a1b5636c09271fb66589f91116efe9625251b3b0ed8ada593e61bb3`。该全量单测覆盖反馈投影变更和本轮新增A19测试，但不能替代全量integration重跑；未发起provider调用、未读取真实holdout。

### A19 正式评价缓存身份回归（2026-09-26）

`run_paired`顶层缓存身份含完整冻结protocol、候选feature-set identity、消融开关/分组identity和attempted-trial计数；seed/fold子运行另绑定protocol、seed、feature set与实验kind。协议hash覆盖数据snapshot、baseline、label、universe、fold、model/seed、cost/research policy、source hash及environment。既有Docker/Qlib配对集成已验证同身份第二次调用复用完成评价及child artifacts、模型/实验manifest时间戳不变，并在引用预测文件被篡改时拒绝缓存复用；`RunStore`单元测试覆盖同run-id异配置拒绝和已完成文件hash损坏拒绝。

新增`test_evaluation_protocol_identity_changes_with_baseline_data_model_or_runtime`，分别改变snapshot、baseline、universe、label horizon、model、seed集合、environment和source hash，验证每项变化都会生成不同`protocol_id`；另测源码或环境变化时旧protocol `require_runtime()` fail closed。专项命令`E:\tools\anaconda\envs\qlib_zhengshi\python.exe -m pytest tests/unit/test_paired_selection.py tests/unit/test_config_artifacts.py -q`：`60 passed in 6.70s`，退出码0。之后全量命令`E:\tools\anaconda\envs\qlib_zhengshi\python.exe -m pytest tests/unit -q`：`977 passed, 6 warnings in 88.69s`，退出码0；warning为既有Pandas FutureWarning。源码hash `878a5df5a57885a454108ae2999e8c783771c41a04f3cc93bebd9018ff7ff995`；环境Python 3.11.15、pyqlib 0.9.7、pandas 2.3.3、NumPy 1.26.4、LightGBM 4.6.0。

同一个Docker/Qlib集成测试现也在干净隔离root下测首次正式paired评价与同identity复用，固定snapshot、protocol、模型、seed、镜像、当前进程；首轮12.590245秒，紧接复用0.408715秒，复用结果完全一致且child manifest时间戳不变，计时约快30.8倍。此单次测量只反映本合成样本的评价缓存调用路径，不外推为策略训练通用加速。provider dispatch为0。JUnit证据`artifacts/research_audits/plan18-a19-cache-20260926/paired-cache-timing.xml`包含计时properties及1 passed/0 failed，SHA256 `5e111aba26e969c7356aa0ad25c2222648b8f7951e7dbe1e8043431bdc8f6917`；命令`E:\tools\anaconda\envs\qlib_zhengshi\python.exe -m pytest tests/integration/test_paired_research.py -q --junitxml=artifacts/research_audits/plan18-a19-cache-20260926/paired-cache-timing.xml`，`1 passed, 29 warnings in 38.11s`。故A19的冻结身份、缓存复用、损坏拒绝及同条件计时验收已有直接证据；没有启动另一物理运行环境，环境差异以protocol身份变化和旧protocol fail-closed合同验证。未发起provider调用、未读取holdout、未重跑正式campaign；W07 usage凭证缺失仍未解决。

在加入A16、A19身份和runtime漂移回归后再次运行全量单元测试：`E:\tools\anaconda\envs\qlib_zhengshi\python.exe -m pytest tests/unit -q`，结果`977 passed, 6 warnings in 88.69s`，退出码0。6项均为已知Pandas FutureWarning；源码hash为`878a5df5a57885a454108ae2999e8c783771c41a04f3cc93bebd9018ff7ff995`。Docker/Qlib配对集成另为`1 passed, 29 warnings in 38.11s`，计时JUnit见本节；未跑全量integration。

### A16 campaign跨进程并发名额上限回归（2026-09-26）

新增Windows `spawn` 多进程回归，8个独立进程同步争抢同一`CampaignLedger`、上限为5的campaign名额。结果恰有5个`begin_trial`成功、3个拒绝，最终event ledger记录5次generation attempt；证明跨进程文件锁下名额分配不会超发。测试`tests/unit/test_research_campaign.py::test_campaign_attempt_cap_is_atomic_across_processes`单项`1 passed in 0.52s`；随后campaign、LLM预算/dispatch、checkpoint恢复和repair quota专项`46 passed in 3.51s`。此为合成账本测试，不访问或修改历史R02/R04/R05，也不验证供应商账单是否完整；usage receipts仍缺。新增内容仅测试与文档，运行时代码未改。之后在包含A16并发回归的当前工作树复跑全量`tests/unit`：`977 passed, 6 warnings in 88.69s`，退出码0；源码hash仍为`878a5df5a57885a454108ae2999e8c783771c41a04f3cc93bebd9018ff7ff995`。6项warning为已知Pandas FutureWarning。
