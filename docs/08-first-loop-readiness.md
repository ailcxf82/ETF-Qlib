# 首轮闭环开发与测试计划

2026-09-14。完整目标仍为01–04全部功能与测试，进入真实RDAgent+Qlib闭环；工程通过不等于G0或投资验收通过。历史实现见[05](05-implementation-status.md)，已复用的数据来源证据见[07](07-data-source-evidence.md)。

## 冻结参数

A股场内现有正常运营ETF；历史成员需按当时已知状态重建。沪深300价格指数、50万元、单边佣金0.03%、单边滑点0.03%；暂按仅比例收费、不另加最低费用。K取可买候选前5%，向上取整非空至少1只、新目标等权；流动性为执行日前20日均成交额20%参与上限，同日双向共享。最大回撤12%为筛选和触发阈值。

月中按15日或之前最近交易日，月末按最后交易日，读取前一交易日收盘信号；h=5标签独立记录。五折扩展训练窗按02 §4.2；2026留出须确认未用于选择。LLM费用已授权无上限，首轮unlimited、api_budget=null、max_trials=1，取消/超时保留。

## 未完成能力与优先级

| 优先级 | 必须完成的工作 | 验收证据 |
| --- | --- | --- |
| P0 | 历史分类/运营/跟踪及成员来源 | 当前元数据全部asof2026-09-05，不能倒填；补齐历史可用时点，24只池外零报价窗口已全部解释（2只清算、22只退市）；仍须补历史分类、跟踪及完整成员可用时点，按真实适用区间重建 |
| P0 | 全池企业行为与合格factor/events输入 | 1769只factor/div共3538响应已齐；组合诊断1751关系通过、0失败、15不完整、3货币ETF收益映射阻塞。严格报价/因子旁路及实际快照/账本已实现并验证；原因子响应独立组装，仍须完整事件/PIT及全池G0验收 |
| P0 | 首轮真实RDAgent+Qlib | 合格快照→固定基线→一次真实提案/编码→Docker→同协议配对、费用压力与独立消融→紧凑反馈；正确拒绝候选也可闭环，回放不能替代真实调用 |
| P1 | 交易限价缺口 | 1769缓存已齐；159657在2023-03-13至17缺5日，新旧接口窄窗恢复均0行。补真实限价或证据支持的规则推导，再验证全报价覆盖/开盘时点/原价边界 |
| P1 | 正式五折、多种子、三模型及容量 | 执行冻结协议，保存逐日账本、暴露、成交和压力报告；复用现有实现 |
| P1 | 独立留出与真实影子验收 | 未见区间独立评估；真实影子至少10交易日并覆盖两调仓节点 |
| P1 | 实际API费用证据 | 已支持预留/缓存/未知费用治理，尚无真实LLM账单；金额封顶transport成本上界另需实现 |
| P2 | 模型包迁移与恢复 | 环境/阶段诊断分离、固定镜像已实现；跨路径引用及恢复链尚未验证 |

2020春节延期的事后日历不能倒推公告前月末决策；当前selection为2023–2025，扩展执行回测到2020须补按公告时点选择的日历政策。

## 当前可核查证据

- [组合关系](../artifacts/data_evidence/action_progress/report.json)：1751通过/0失败/15不完整/3收益映射阻塞。148只增量复查、1621只未按最新代码全池重验，非正式G0。
- [份额输入v25](../artifacts/data_corrections/share_announcements_v25/assembly_report.json)：144事件，绑定原公告、实际查询及原因子响应，保留159922拆分后现金基准。精确整数公式只执行整数结果，其他分配需原公告规定的floor/ceil或实际明细。159919原分红日期/金额未改，已用[原发行人PDF](../artifacts/data_evidence/159919_primary_dividend_recheck/report.json)修正证据绑定。
- [历史窗口](../artifacts/data_evidence/historical_window_candidates/report.json)：24只池外空报价已有解释——[2只窗口前清算](../artifacts/data_evidence/prewindow_operations/report.json)、[22只窗口前退市](../artifacts/data_evidence/retired_prewindow_batch/report.json)。退市不等于基金运营终止；保留510700、512110两处当前声明与原交易所日期差异。原24次空报价缓存不改、不重查，未删除成员或发布全池PIT。
- [15只上市窗口](../artifacts/data_evidence/listing_window_batch/report.json)：15份完整原上市公告书与实际公开查询绑定；独立交易日历、原bin全部日期及旧源哈希核对通过。12只截止日前未到公告上市日，3只仅上市首日报价；6份公告截止日前可用、9份仅作事后覆盖诊断。未来计划上市不冒称已经完成，企业行为诊断的15项不完整尚未改成通过。
- [严格修订输入](../artifacts/data_corrections/quote_revisions_v1/review.json)：4处收盘差异及515580量额获双源确认，核心旁路逐字段核对原值、数值边界、原文件/响应哈希及因子边界；替换报价、重算涨跌字段并保留其他factor倍率。[真实全期复查](../artifacts/data_evidence/micro_quote_revision_recheck/report.json)4/4通过，原分红/v25份额/0.0005容差不变。实际快照和Qlib独立净值验证通过；未改原数据或推造企业行为。[原因子输入](../artifacts/data_corrections/tushare_factors_v1/assembly_report.json)从1769份原缓存独立组装1344565行，配置先导入原因子再应用严格旁路，不使用失败诊断临时文件。

- [货币收益语义](../artifacts/data_evidence/money_income_semantics/semantic_report.json)：三只Tushare零金额缺日期记录不证明零收益；嘉实20240617月度公告仅A类，不能套给场内H类。已补[完整收益区间](../artifacts/data_evidence/money_daily_income/assembly_report.json)6375条（1967/1967/2441），三只2019-12-30至2026-09-04均覆盖2441自然日、无缺日；159001、159003各341个合并区间，511960逐日完整。核心money_income.py保留区间、单位及负/零收益，禁止均摊为日收益；24项专项通过。159001一页超时仅按真实缺口恢复2020窄窗，原失败收据保留。159001收益页已直接确认每百份单位，原网页字节/解码哈希绑定，当前回执不倒填历史。收益账户已接入实际Qlib/独立账本：87项相关测试及新增防护25项通过（重叠范围），两项实际Qlib合成收益回测通过；未付复利、面值兑付、部分/全卖及正负余额核对一致。实际尾数分配、两只逐日明细及披露时点未齐，不发布合格事件输入。

- 159657已有原上市公告、初始合同与招募说明书，2023-03-13至17限价仍缺5日；新旧接口窄窗均0行，尚无明确适用比例，不倒填猜测。
- [最终回归](../artifacts/tests/source_revision_final_regression.xml)：631单元+15实际Qlib/快照/旁路/基线检查共646通过，0失败/错误/跳过，148.70秒、483第三方警告。修复回归发现的Windows目录发布访问拒绝：同一验证完毕的暂存目录受限重试，持续拒绝不发布成功，临时目录名用UUID。未重查来源或触发付费调用。[当前预检](../artifacts/first_loop_metadata_scope_preflight.json)剩metadata/events/PIT三项，0真实LLM、0真实训练；全池G0尚未通过。

## 开发及测试顺序

1. 严格修订旁路及4只全期复查已完成；补历史成员及完整事件资格输入、两只合并收益的逐日明细、实际尾数/结算规则、历史披露时点及五日限价来源；15只上市窗口已有证据，9份事后公告不倒填，复用既有单位与日历证据。
2. 汇总全目标输入、建立真实快照；验证事件/因子关系、覆盖、时点和证据闭包，未通过仅发布诊断。
3. 执行固定基线和首轮真实候选，完成配对、压力、独立消融和紧凑反馈。
4. 按04的G1–G4完成正式复现、投资及影子验收。

```powershell
python -m etf_ml.cli first-loop-readiness --config configs/data/tushare_first_loop.yaml
python -m etf_ml.cli build-data --config <完整配置>
python -m etf_ml.cli baseline --config <完整配置> --snapshot <合格真实快照>
python -m etf_ml.cli research-factor --config <完整配置> --snapshot <合格真实快照> --max-trials 1
```

2026-09-14收益账户改变执行器/持仓/独立账本/保存路径，[整目录首次回归](../artifacts/tests/income_full_regression.xml)778项中768通过、10失败，0错误/跳过；8项为原生Qlib Position无income_book，2项为Windows快照暂存路径过长。已兼容可选收益账户并缩短唯一UUID暂存名；[受影响执行、收益及完整留出恢复重测](../artifacts/tests/income_failure_fixes.xml)72项全部通过，0失败/错误/跳过，780.10秒、85第三方警告；[闭包核对](../artifacts/tests/income_regression_closure.json)确认原10失败用例全部被重测且通过，绑定当前代码和报告哈希。修复后未重跑整个目录，不声称当前整目录778通过。[快照/路径防护](../artifacts/tests/income_snapshot_publication.xml)19项通过，保持原子发布和源目录只读。87项相关单元、25项新增防护及两项实际Qlib已通过，范围重叠不相加。159001单位归档及更新的6375条收益闭包重复哈希一致、零重查；无新增真实LLM调用。预算与组合参数阻塞已消除；正式快照仍缺历史metadata/events及PIT证明；原因子旁路和严格修订已接入。

2026-09-14收益快照接线：新增income_path来源闭包/规则时点、完整及研发/留出收入视图，接入共享基线/候选/辅助/费用压力与留出入口；每日纸面正负余额、当日版本、预算/权益、历史改写校验已实现。[全部单元及相关实际基线回归](../artifacts/tests/income_snapshot_final_regression.xml)702单元+4实际Qlib共706通过，0失败/错误/跳过，319.16秒315第三方警告。标准Ridge及费用压力账本实际非零收入与独立余额/份额算术一致；旧三模型基线及正负Qlib收益同样通过。[来源及冻结收入复查](../artifacts/tests/income_snapshot_frozen_prefix.xml)27通过40.32秒，其中22与前报告重叠，新增5实际重建快照场景：不改历史放行，金额/披露时间/规则版本/规则发布时间改写拒绝。[闭包](../artifacts/tests/income_snapshot_closure.json)绑定报告与当前代码哈希2e288c39de7502c66b26760b0fabafebbcd39d9904b8c805ba2f4d68a4ad0aa0，确认首轮1个记录类型断言失败已复测通过，整个集成目录未重新运行。新增收益的完整冻结留出/每日服务链仍待集成验收，货币投资池总回报标签仍待另验；不冒称真实收益来源已合格。此阶段不发布真实收益输入，不声称G0或真实机器学习闭环。

收益审计组合失败/坏索引防护（T42）已在14fe版713项回归及当前61bb版723项中通过；旧报告为对应历史代码状态，当前最终证据以上述完整成员覆盖闭包为准。

P0成员覆盖已新增正式快照门禁（T43）：历史元数据缺失/未来可用的有报价标的不再隐式缩小全池；审计记录全部未知报价数量/标的和日期样例，发布前拒绝，保持独立历史池诊断及已知池外资产。定向58项通过21.23秒、1第三方警告（metadata_quote_scope_targeted.xml）；[最终回归](../artifacts/tests/metadata_quote_scope_regression.xml)715单元+8实际基线/严格修订真实快照/CLI共723通过，0失败/错误/跳过，173.89秒319第三方警告（86620 exit0）。[闭包](../artifacts/tests/metadata_quote_scope_closure.json)绑定当前固定代码61bbdab106fc64cb2f1df5ee92d56b87bb071129776eb2db788b0f2abd101a5b与报告哈希，当前全单元包含新增收益及六项完整成员覆盖场景，未重跑整个集成目录。前收入审计版本14fe最终713项全通过304.32秒315警告，不与新成员门禁代码状态混淆。真实历史来源仍须补齐，未用局部元数据通过原完整目标G0。
