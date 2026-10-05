# 正式 ETF 因子研究闭环优化：工程规格与验收方案

日期：2026-09-24。版本：设计 v1.0。状态：**E01–E03 已实现；E04 日级只读风险归因已实现；E05 具备有界 campaign 账本核心；E06 具备跨运行硬上限。多重试验校正、机制配额和真实生成/正式研究尚未验收。**

依据：r1–r25 运行产物、最后一次诊断反馈及当前工作区源码。目标是修复“可以运行、但不能完整学习失败且正式接受条件不完备”的研究闭环，提高找到合格因子的机会；不承诺一定存在可接受因子。

实施记录（2026-09-24）：E01 在正式首轮、通用 research-factor 入口、session 构造及 readiness 中加入压力阈值 fail-closed；E02 反馈投影保留全折与折/情景风险，优先最新可比经济卡，必需上下文超预算时阻断，且 code 阶段不为无关历史卡失败；E03 在登记工件根内核验真实模型路径并保留失败原因；E05 分列 model_seed/bootstrap_seed。后续批次新增每日 risk_checks 与独立账本对齐的候选/基线日级风险及执行差异；新增可选 campaign_id + 必填正整数总尝试上限、追加式哈希链事件、兼容组分层、去重定义/完成评估/复用计数和费用/用量覆盖统计；上限跨运行原子预留，恢复以 checkpoint 对账。E06 目前只落实总尝试硬上限与已有因子身份/质量准入；机制/窗口/修复配额、无进展阈值和预筛淘汰阈值仍待预先冻结。未重建历史派生索引、未修改 r1–r25 工件、未启动真实研究。

关联：[13 工程规格](13-llm-factor-engineering.md)、[14 测试标准](14-llm-factor-test-standard.md)、[15 因子能力与去重](15-rdagent-factor-capability-implementation.md)。本文是上述方案的运行证据驱动补充，不重新实现身份、缓存、调度框架。文档 15 的 W04–W07 与本文重叠的工作合并交付，不建两套台账或两套准入逻辑。

## 1. 结论、目标与授权边界

优先级结论：先修验收协议、反馈投影和证据复用，再诊断风险与搜索方向，最后开展有界的真实研究。继续增加轮次不能替代这些修复。

| 层级 | 成功定义 | 不能据此声称 |
| --- | --- | --- |
| 工程可用 | 协议完整、接受分支可达、反馈进入下一请求、证据可核验、恢复不重复计费 | 找到了有效因子 |
| 开发期研究合格 | 新冻结协议下五折、三种子、消融、成本压力、风险及换手门禁全部满足，正式评估为 accepted | 独立样本外有效或可以投资 |
| 独立确认 | 候选和方法冻结后，通过有资格的独立留出验证；留出独立性有证据 | 未来收益有保证 |
| 投资准备 | 数据资格、独立确认、前瞻 OOS、执行及风控验收分别完成 | 自动授权交易或连接券商 |

本阶段范围：工程规格、后续最小修复、离线回放、风险归因及验收设计。**本文件本身不授权代码实施、付费 LLM、模型训练、回测、恢复自动监控或启动 r26。** 已结束的 25 轮作为历史研究保留；后续实验须另行授权。

不可变边界：

- 原始 `D:\qlib_data\etf_qlib_data` 只读。
- 保留快照 `artifacts/formal_tushare/data/8b384b5d9f8fa10d3d7b6aebbf11fd9463dc76cc9fe203d38dfc6f5ad4322831`。
- 保留基线 `artifacts/runs/tushare-formal-baseline-20260921` 及 r1–r25 的全部报告、协议、请求、模型引用和账本。
- 不修改旧协议阈值、不回填旧报告为 accepted、不重写 checkpoint 或旧卡片哈希。
- 新协议、新提示词、新源码均产生新身份；不能把新结果写进旧会话或假装与旧实验完全可比。
- 因子生成与开发期选择链路不接触最终留出结果；独立验收者按冻结流程检查留出，不向生成端回流。系统不连接券商、不输出执行买卖指令。

## 2. 运行基线与问题证据

### 2.1 统计口径

截至本次检查，共 25 个 `tushare-formal-first-loop-*` 运行目录：23 份最终报告，其中 12 份 completed、11 份 incomplete；另 2 个目录无最终报告。12 个 completed 候选均 rejected，accepted 为 0。**25 个目录不等于 25 个完成经济评估的独立候选。**

23 份报告的 `billing.unknown_cost_calls` 合计 61。这只是已有最终报告覆盖的记录小计，不能当作所有物理请求总数；费用未知不是费用为零。早期运行协议不同，不跨协议平均收益、合并显著性或比较总分。r18–r25 的共同协议 ID 为 `c4f0300de830c06ace329e92c4598dd82550e9e495c646c6c67a62952302cec0`。

[r25 最终报告](../artifacts/runs/tushare-formal-first-loop-20260923-r25/first_loop_report.json) 为 completed/rejected，`formal_g0_passed=false`、`holdout_evaluated=false`、`investment_accepted=false`。报告中的 `investment_acceptance_eligible=true` 不能覆盖这三个实际结果字段。[r25 冻结协议](../artifacts/runs/tushare-formal-first-loop-20260923-r25/research/sessions/tushare-formal-first-loop-20260923-r25-research/protocol.json) 另有 `holdout_independent=false`，独立确认须重新核实资格，不能仅按日期命名为 OOS。

### 2.2 已确认问题与推断边界

| ID / 优先级 | 已确认事实与定位 | 影响及边界 | 工作包 |
| --- | --- | --- | --- |
| F01 / P0 | `protocol.py` 默认 `stress_min_excess_return=None`；`first_loop.py` 构造协议未赋值；r25 固化为 null；`selection.compare` 在无其他违规时仍返回 inconclusive | 当前首轮协议不具备 accepted 路径。已有 rejected 仍有效，补阈值不使历史失败自动合格 | E01 |
| F02 / P0 | `prompting.select_cards` 总字节上限 6400，整卡超限直接跳过；10 张正式经济失败卡的现有投影约 7002–7893 UTF-8 字节 | 这些卡不能入选；抽查 r18、r20、r23、r24、r25 的 hypothesis/proposal 请求，仅出现 null、lottery_neg_skew_60、lottery_reversal_20 对应的小卡，未传入近期正式经济反馈；不是对全部历史请求的普查 | E02 |
| F03 / P0 | `memory_index._verify_evaluation` 将模型根设为 `allowed_root / "models"`；实际模型在 run 的 `research/executions/<id>/artifacts/models`；r25 只读复现路径越界错误 | 索引中 10 张正式 rejected 卡的 `evaluation_evidence_verified=false`，阻断完整证据复用；不能由此推断每个重复提案均重训 | E03 |
| F04 / P1 | r25 基线、候选各有 12/15 个 fold/seed 回撤超过 12%；`qlib_runner.py` 非调仓日提前返回，风险触发判断在其后 | 组合约束与风险执行需单独诊断；不能把所有超限都归因于因子，也不能断言调仓频率解释了全部超限 | E04 |
| F05 / P1 | `controller.py` 设置会话 `attempted_trials=index+1`，每 run 上限为 1；r25 统计仍记 1 | 单会话计数不能表达多轮选择历史，不能把 25 轮搜索当单次检验 | E05 |
| F06 / P1 | `paired.py` 写入模型 seed 后展开统计结果；`statistics.py` 返回的 bootstrap seed 覆盖同名字段，r25 时间块统计全部 seed=42 | 时间块统计的种子标签失真；`evaluation.json` 的 42/43/44 配对仍存在，不能据此否定全部回测数值 | E05 |
| F07 / P1 | r25 `actual_cost=null`、`cost_unknown`，同时 `measured_cost="0"` | 0 是已知金额小计，不是整轮免费；报告需保留覆盖率与未知次数 | E05 |

代码证据：[协议](../src/etf_ml/research/protocol.py)、[首轮入口](../src/etf_ml/research/first_loop.py)、[选择器](../src/etf_ml/research/selection.py)、[提示词](../src/etf_ml/research/prompting.py)、[记忆索引](../src/etf_ml/research/memory_index.py)、[准入策略](../src/etf_ml/research/search_policy.py)、[风险执行](../src/etf_ml/backtest/qlib_runner.py)、[控制器](../src/etf_ml/research/controller.py)、[配对评估](../src/etf_ml/research/paired.py)、[时间块统计](../src/etf_ml/research/statistics.py)。源码可能继续变化，实施验收必须保存实际文件哈希，不能仅引用当前行号或 Git HEAD。

### 2.3 候选质量与系统缺陷分开判断

- [r23](../artifacts/runs/tushare-formal-first-loop-20260923-r23/first_loop_report.json) 的 `vwap_gap_reversal_20` 在 A–E 五折的跨种子超额收益增量中位数约为 0、+0.141、+7.449、+2.691、+0.331 个百分点，4/5 折为正。它是可追踪的开发期线索，仍未通过正式联合门禁，不是已选赢家。
- r25 的 `overnight_intraday_spread_20` 对应约为 0、+1.551、+0.876、0、−7.206 个百分点；15 个配对增量的算术平均约 −0.525 个百分点。均值仅为描述，不替代逐折/逐种子门禁，也不将三种子视为三个独立时间样本。
- r25 的七项拒绝码是 `ablation_not_confirmed`、`absolute_risk_limit`、`cost_pressure_risk_limit`、`drawdown_deterioration`、`no_majority_fold_increment`、`seed_instability`、`turnover_deterioration`。

后续复查应从最终报告的 `candidate_decisions[].reports` 解析同目录的 evaluation、ablation、cost-stress、诊断和日收益证据，不仅看摘要。F01–F03 修复旨在让系统有正确的判定与学习能力，并不保证 F04 或候选真实弱效应消失。

## 3. 目标闭环与接口原则

目标顺序：协议完整性预检 → 可信记忆与预算投影 → 机制假设 → 完整定义准入 → 便宜质量检查 → 正式配对评估 → 完整反馈 → checkpoint 提交 → 派生索引/台账刷新 → 下一轮。

遵循以下原则：

1. `selection.compare` 是正式选择结论的唯一来源；摘要、LLM 评价、筛选分数不能提升为 accepted。
2. 完整工件是事实，摘要是投影，索引是可重建视图。先修现有模块，不增加独立数据库或另一套编排服务。
3. 技术失败、经济拒绝、不可判定、复用、未完成分别计数；真实拒绝不得进入“修复到通过”。
4. 不完整协议和身份不一致应在昂贵工作前 fail closed；历史不完整协议仍可只读解释。
5. 离线工程验收、真实提案能力、经济有效性独立发布；测试数不能代替投资证据。

## 4. E01：正式协议完整性与 accepted 可达性（P0）

### 改动范围

扩展 [contracts.py](../src/etf_ml/contracts.py)、`research/protocol.py`、`research/first_loop.py` 和 [cli.py](../src/etf_ml/cli.py) 中相关入口；复用既有运行预检，不新增第二套参数来源。

### 实施要求

1. 在配置层显式承载成本压力最低超额收益要求，并映射至 `ComparisonProtocol.stress_min_excess_return`。字段所在配置层、schema 版本和单位必须唯一且有文档；禁止各入口自设默认数值。
2. 阈值与现有 `cost_stress[*].excess_return` 同口径：[metrics.py](../src/etf_ml/backtest/metrics.py) 当前计算为评价区间内策略累计收益减基准累计收益，使用比例值，非年化、非候选减基线。实施时用手算夹具及回测基准输入核准并冻结，确认压力场景仍使用同一评价日期与基准。不能混用收益百分数、百分点和比例，也不能把“允许低于基准多少”混成“允许低于基线多少”。
3. 正式新运行必填且必须为有限数值；null/NaN/Inf、缺消融能力、缺折/种子或非法成本场景，在 LLM 分派及训练/回测前失败。配置级校验尽可能放在数据重计算前；依赖环境的预检另行验证 Docker 和固定镜像。
4. 旧协议保持原字节、哈希和读取语义；旧 null 协议只能解释为历史不完备，不能静默补值恢复正式研究。
5. 正式比较保留 rejected 优先级：存在风险等违规时仍 rejected；无违规但历史证据缺失时 inconclusive；只有全部条件满足时 accepted。
6. 用人工可核算、非投资结论的测试夹具覆盖三个分支。不得用降低实际阈值让 r25 成为测试中的“合格因子”。

交付：版本化配置契约、统一预检、入口接线、结构化 `protocol_incomplete` 错误及完整性报告。**阈值具体数值待负责人依据目标预先确认；不能观察候选结果后倒推门槛。**

## 5. E02：可送达、保真、可追踪的失败反馈（P0）

### 改动范围

以 `research/prompting.py` 为主，扩展 [diagnostics.py](../src/etf_ml/research/diagnostics.py)、[feedback.py](../src/etf_ml/adapters/rdagent/feedback.py) 的确定性摘要和现有请求 envelope。保留完整反馈原件，不让 LLM 自己总结自己的失败。

### 卡片投影设计

拆成“必需核心 + 可选细节”，不是简单提高 6400 字节上限：

| 部分 | 必需内容 | 压缩规则 |
| --- | --- | --- |
| 身份与资格 | definition/trial、协议/基线/快照、可比性、经济或技术类别、来源哈希 | 不混淆不同上下文；向模型发送安全短标识，本地保留完整映射 |
| 机制与定义 | 机制、公式、窗口、与基线差异 | 超长自由文本可定额截取并标 truncated，不能改变公式语义 |
| 决策 | status、全部拒绝码、unknown 状态 | 不能只留主失败原因或删除不利项 |
| 五折摘要 | 每折增量中位数、范围、有效/正/零/负种子数 | 必须覆盖全部冻结折，不按 `[:6]` 截取前几行 |
| 风险与执行 | 基线/候选超限数量、最差值、增量恶化；排名/入选/权重/成交变化计数 | 不用平均值遮蔽最差折；缺证据为 unknown |
| 细节引用 | 完整 fold/seed、消融、成本压力的工件 ID/哈希 | 默认不展开明细，按可解释规则补充 |

### 选择、预算与恢复

1. 有已提交的经济反馈时，hypothesis/proposal 阶段至少保留最新一张可比经济卡的核心。最近记录与相关记录继续承担不同职责；技术失败单独配额，不能挤掉全部经济反馈。
2. 若仅有不可比历史，保留明确标记的机制教训，不把其数值当当前协议证据。code/repair 阶段不强求携带无关研究历史。
3. UTF-8 字节预算与 token 预算分别核算。先压缩可选细节，再减少非必需卡；不能让后续 token 裁剪再把保证送达的核心弹出。
4. 连必需核心及任务契约都容纳不下时，返回 `prompt_required_context_over_budget` 并阻断发送；不静默退化为空记忆。预算值及核心大小上限在离线夹具上校准后冻结。
5. 保存本地选择清单：卡片输入集合哈希、included/omitted ID、原因码、层级、字节数、估计 token、投影版本、最终 payload_hash。不得把完整审计清单再次塞进提示词。
6. 同输入、同版本产生字节一致的 prompt；恢复使用已冻结检索视图，不能因另一运行新增卡片改变在途请求。
7. 留出字段、敏感路径和凭据继续过滤。每个数值必须能追溯到已提交开发期工件。

交付：核心摘要 schema、预算策略、选择 manifest、跨提交/下一请求回放证据。验收必须检查**实际 transport request 的 user_prompt**，不能只测 `select_cards` 的返回值。

## 6. E03：严格且可解释的证据复用（P0）

### 根因修复与边界

当前 r25 的顶层五份评估证据哈希匹配，但模型引用根推导错误。修复 `memory_index.py` 与 `paired.py` 的引用契约；不得把 `evaluation_evidence_verified` 强制改成 true，也不得通过放宽到磁盘根目录解决。

1. 从可信执行 manifest/请求产物解析 evaluation、子运行及模型根，验证其来源与注册 research root 的关系。不依赖“向上两级目录”的偶然布局。
2. 每个根先解析绝对路径，再限制于显式登记的 artifact root；检查路径穿越、符号链接/Windows junction 出界、未注册来源及伪造 manifest。模型与子运行还需绑定相同执行身份，不只检查“位于 artifacts 内”。
3. 逐项验证 checkpoint → trial → research card → paired/evaluation → child/model manifest 的哈希与身份。全通过才允许跨运行复用。
4. 采用结构化校验结果，至少包括 verified、reason_code、failure_stage、evidence_id；例如 `model_root_unresolved`、`reference_outside_allowed_root`、`hash_mismatch`、`identity_mismatch`、`missing_artifact`。布尔兼容字段可保留，但报告不能只返回 false。
5. 历史 manifest 足够时通过只读兼容适配器解析；不够时保持不可复用并说明原因，不能根据相似目录猜测通过。
6. 同定义、同评估身份、完整证据的 accepted/rejected 都可以复用；不同源码/协议/样本/基线若改变评估身份，只能作为历史反馈。不得为了命中而删掉身份字段。
7. 新版本派生索引在独立位置重建、对账后切换，保留旧视图。原卡片、原 trial、原报告和原哈希不变；失败时回到旧派生视图并阻断依赖错误索引的新分派。

交付：可信根解析、完整性诊断、派生索引重建/回滚路径、真实目录布局集成测试。合成同身份复用需证明没有新 coder、训练、回测和付费调用；不能要求源码变化后的 r25 跨新身份强行命中。

## 7. E04：把因子增量与组合风险问题分开诊断（P1）

先做只读日级归因，不直接调整策略。现有策略 12% 是触发参数，不能因代码写了 12% 就宣称实现回撤硬上限；价格跳空、执行延迟、现金/应收、涨跌停及流动性限制均可能影响结果。

### 日级诊断交付

扩展既有诊断输出，按日期、折、模型种子保存：权益/高水位/回撤、可见信息时点、计划调仓日、风险是否被检查、触发时点、订单生成与实际成交时点、未成交原因、现金/应收比例、候选与基线持仓差异。对账日收益与原交易账本，不复制一套收益计算器。

每个超限事件回答：最早何时可观察到风险、当日是否运行风控、何时最早可合法成交、未执行或延迟的原因、候选相对基线的增量。区分代码事实、账本支持的归因和仍未确认假设。

### 两层评价，但只有一个正式 accepted

- 研究诊断层：覆盖率、残差信息、跨折增量、种子一致性、基线继承风险。用于解释和提出下一假设。
- 正式策略层：继续执行冻结的绝对风险、逐 fold/seed 的回撤/换手恶化、消融及成本压力要求。诊断层优秀但正式层失败，仍 rejected。

若证据支持“独立于调仓日检查风险”，可提议日级风险检查、预防性暴露缓冲或更低仓位上限；这些改变应另开策略/协议版本并重新建立匹配基线，需负责人批准。日级检查也不保证交易受限时绝不越过 12%。不在当前候选搜索中同时调模型、风险阈值与组合权重。

5 日标签与月中/月末调仓的期限差异只作为待验证问题。若试验不同标签或持有期，单独冻结协议与对照，不能混在同一次因子优选中挑最好结果。

## 8. E05：跨运行统计、种子身份与用量审计（P1）

扩展文档 15 的 W04 台账，沿用 checkpoint 为权威、只追加事件和幂等恢复，不新增并行计数系统。

| 字段/结构 | 语义与规则 |
| --- | --- |
| campaign_id / compatibility_group_id | 前者绑定有界研究计划；后者绑定可比协议、基线和数据条件。跨组只汇总尝试历史，不合并收益统计 |
| generation_attempts / unique_definitions / completed_unique_evaluations / reused_evaluations | 生成次数、定义数、完成评估数、复用次数分开；失败与中断也留事件，复用不算新独立检验 |
| session_attempted_trials / campaign_attempted_trials | 保留原会话计数，增加跨运行口径；历史无法恢复部分为 unknown 并报覆盖率，不机械填 25 |
| model_seed / bootstrap_seed | 两字段分别保存，不让字典展开覆盖；主键使用 fold + model_seed + evaluation_id |
| cost_known_subtotal / unknown_cost_calls / usage_coverage | 已知金额小计、未知费用次数、服务商用量覆盖率分开；不把 tokenizer 估计写为 provider usage |
| event_id / source_hash / committed_sequence | 幂等与追溯；发送后结果不明保留 uncertain，禁止当作零成本直接重发 |

campaign ledger 已作为新派生工件实现，`research-factor` 与 `first-loop` 都使用 `--campaign-id` 和 `--campaign-max-trials` 启用；二者必须同时给定，campaign 身份/上限不可变。正式 `first-loop` 强制要求有限 campaign，先核对 cap 未耗尽，再准备本轮数据；每次 first-loop 仍固定一个 trial。实际 trial 名额在不同 run 间原子占用，避免并发超额派发。追加式事件链保存已提交 trial 哈希、协议兼容组、每个 proposal 的定义及每个候选经济评估/复用与用量成本摘要；checkpoint 是事实来源，恢复时幂等补齐。未完成 trial 会将 usage audit 标记为 `partial` 并单列未决 slot，不能据此将未知成本解释为零。总次数是 generation/trial 尝试数，不等于唯一因子定义数（单个 proposal 可包含多个定义）。`unlimited` 只表示没有金额硬上限；campaign attempt cap 不限制 provider 物理调用数或总费用，未知费用停机与调用/费用配额仍待实现。历史运行没有 campaign_id，不回填、不推定属于同一 campaign。model_seed/bootstrap_seed 仍独立保存；旧 `seed` 修复只允许新增派生视图，旧 `time_block_statistics.json` 不重写。

正式 first-loop 每轮固定一个 trial；同一 campaign 后续轮次必须使用新的 `--run-id`，并保持 `--campaign-id` 与上限不变。就绪检查可同时验证 campaign 参数，例如：`python -m etf_ml.cli first-loop-readiness --config configs/data/tushare_formal_first_loop.yaml --campaign-id formal-five --campaign-max-trials 5`。真实运行时将相同两个 campaign 参数传给 `first-loop`；达到上限后，账本会拒绝新 trial slot，调用端应停止继续创建 run。

多重试验控制：全局计数只是透明化，不是校正方法。恢复研究前应预先约定搜索上限、选择统计及独立确认方式；若选用 DSR/PBO 等方法，需要先证明输入、样本长度、候选相关性处理与适用条件，不能给现有 bootstrap 换名称宣称已纠正选择偏差。三种子主要反映训练随机性，不是三倍独立时间样本。

候选冻结后仅按预先规则开展独立确认，不能反复看留出再返回生成端修改因子。`holdout_independent=false` 或使用历史不清楚时，不发布“独立样本外通过”。

## 9. E06：以机制与便宜筛选提高搜索效率（P2）

依赖 E01–E05 的关键验收完成；接续文档 15 的 W05–W07，不扩大为无限候选工厂。

1. 提案必须说明机制、可见字段、时间因果、相对已有特征的差异、预期改善折/市场状态和可证伪条件。近期失败反馈要转化为明确的改变维度，不能只更换同类 OHLCV 公式名称。
2. 采用阶段筛选：定义/时点准入 → 覆盖与非恒定检查 → 训练/开发区间冗余和信息诊断 → 正式配对。初筛阈值与是否为硬淘汰须预先冻结；仅“高相关”不能证明定义等价或无条件增量。
3. 初筛保留固定样本与 PIT 资格；不按目标收益修改可交易池、缺失样本或方向。IC、残差 IC、分布稳定性只是开发期线索，不替代组合层消融。
4. 候选要记录排名变化 → 入选变化 → 权重变化 → 真实成交变化。只有排名变化而持仓不变时，下一步应解释传导瓶颈，不直接声称 alpha 无效或已有效。
5. 机制家族、窗口近邻、复测、技术修复设独立配额。复测要求注明改变维度或新证据；经济拒绝不触发代码修复。
6. 已实现有限 campaign 的跨运行 trial 尝试上限；到达上限正常停止。候选定义、provider 物理调用、技术修复、时限、未知费用停机和无进展阈值仍未形成完整联合配额。仅有 trial cap 不足以批准付费真实研究，不能以“直到合格”为无限运行条件。
7. 真实生成对照复用文档 15 的代表上下文与盲评设计，比较合法唯一提案率、反馈送达率、首次实现率、技术失败率、完成唯一评估/物理请求、耗时、未知费用覆盖。总调用更少但有效候选更少，不直接算效率改进。

交付：阶段计数、可解释拒绝原因、机制配额、下一轮任务记录和有界 campaign 报告。accepted 数仍单列，不能用优化后的评分替代。

## 10. 验收矩阵

以下 O01–O20 是新增验收场景。已执行项和结果见本节末的实施验证记录；其余仍是计划，沿用文档 14 的 L1 单元、L2 真实接口回放、L3 真实生成、L4 正式研究分层。未执行不等于通过。

| ID | 工作包 / 层级 | 输入或动作 | 必须满足的断言 |
| --- | --- | --- | --- |
| O01 | E01 / L1 | null、NaN/Inf、消融执行能力不可用或非法压力场景 | 正式入口在分派及训练前阻断；物理请求、训练和回测计数为 0 |
| O02 | E01 / L1 | 人工可算的全满足、风险违规、旧证据缺项夹具 | 分别 accepted、rejected、inconclusive；边界值/单位一致 |
| O03 | E01 / L2 | first-loop 与普通 research 入口同配置 | 冻结到同一门禁参数；旧协议读取哈希不变，不允许静默补值恢复 |
| O04 | E02 / L1 | 10 张超过 6400 字节的真实形状卡、技术卡、中文/长文本 | 最新可比经济核心送达、五折齐全、全部拒绝码保留、预算不超 |
| O05 | E02 / L2 | 已提交 trial → 重建索引 → 下一 hypothesis/proposal 实际 request | 请求正文含正确反馈；不能被 token 二次裁剪移除；哈希与清单一致 |
| O06 | E02 / L1 | 必需核心超限、留出字段混入、未知数值 | 明确阻断或安全过滤；未知不变 0，不发送留出内容 |
| O07 | E02 / L2 | checkpoint 前后中断；其他运行新增历史 | 同身份恢复 prompt 字节及检索视图一致，无重复调用 |
| O08 | E03 / L2 | 真实目录形状、真实 manifest/hash、同身份已拒绝候选 | 证据 verified 并复用，无新 coder/训练/回测/付费调用 |
| O09 | E03 / L1–L2 | 越界/junction、伪造根、缺模型、哈希改变、未注册来源 | 全部拒绝复用；结构化失败原因准确，不放宽安全根 |
| O10 | E03 / L2 | 新索引构建中断/切换失败；旧 manifest 缺信息 | 历史原件不变、可回滚；缺证据保持 unverified，无盲目重复评估 |
| O11 | E04 / L2 | 非调仓日回撤、跳空、交易受限的手算路径 | 正确还原检查/触发/下单/成交时点；不使用当时不可见价格 |
| O12 | E04 / L1–L2 | 基线超限但候选改善、候选恶化、无成交证据 | 诊断分层准确；正式选择结果不被诊断提升；缺失归因 unknown |
| O13 | E05 / L1–L2 | 三个单 trial 运行、重复、失败与恢复 | 会话/跨运行/唯一评估计数分离且幂等；跨协议不合并收益 |
| O14 | E05 / L1–L2 | 五折 × 42/43/44 模型种子，bootstrap=42 | 15 个不同 fold/model_seed 组合；bootstrap 标签无覆盖；数值映射正确 |
| O15 | E05 / L1 | actual_cost=null、已知金额 0、响应丢失 | 分别 unknown、真实零、uncertain；覆盖率分母包含未知尝试 |
| O16 | E06 / L2 | 不同机制/窗口近邻、相关但不等价、达到配额 | 不误杀非等价定义；按冻结规则停止；不因经济失败无界修复 |
| O17 | 全部 / L2 | 固定镜像、实际 FactorRegistry/ETFWorkspace、ReplayTransport 多轮 | 提交、反馈、准入、复用与中断恢复串联；无网络/付费请求 |
| O18 | 全部 / L2 | 对历史快照、基线、协议、报告、checkpoint 前后清单对账 | 原始路径、大小和哈希不变；新增内容仅在批准的新输出目录 |
| O19 | E06 / L3 | 经授权的冻结上下文真实生成对照 | 全请求/失败完整留痕，质量和用量分别报告，不挑选成功案例 |
| O20 | 全部 / L4 | 经授权的新正式 campaign | 完整联合门禁决定 accepted/rejected/inconclusive；独立确认另验收 |

### 测试落点与执行要求

优先扩展已有 `tests/unit/test_research_runtime_preflight.py`、`test_llm_factor_v1.py`、`test_research_memory_v2.py`、`test_factor_admission.py`、`test_first_loop_reuse.py`、`test_v2_diagnostics.py` 及 `tests/integration/test_paired_research.py`、`test_accumulated_research.py`。全链路用真实目录、序列化和注册生命周期；mock 仅用来观测调用是否发生，不能 mock 掉被测路径解析/身份校验本身。

可新增聚焦的协议完整性、campaign 计数与统计种子测试文件，名称以实际实现为准。下面是已有测试的回归入口示例；单独运行这些文件不代表 O01–O20 全覆盖：

```powershell
& 'E:\tools\anaconda\envs\qlib_zhengshi\python.exe' -m pytest tests/unit/test_research_runtime_preflight.py tests/unit/test_llm_factor_v1.py tests/unit/test_research_memory_v2.py tests/unit/test_factor_admission.py tests/unit/test_first_loop_reuse.py -q
```

实施验证记录（2026-09-24）：Docker/Linux 守护进程及冻结镜像预检通过。`pytest tests/unit -q`：829 passed、6 warnings；Docker L2：`test_rdagent_research.py`、`test_paired_research.py`、`test_accumulated_research.py` 共 3 passed。新增 E04/E05 单测覆盖逐日权益/回撤/成交失败归因、campaign cap 原子预留、多 proposal 定义/评估计数、兼容组隔离、事件链完整性、成本未知及 provider usage 覆盖；集成验证 RDAgent 中断恢复/campaign、配对回测/风险归因和累积 worker。对应 O11、O13–O15、O17 的相关夹具/回放通过，但 O13/O14/O15 不是所有历史与独立故障情形的全覆盖。集成数据使用临时夹具、ReplayTransport/free-only；不代表真实候选 accepted，不代表 O19/O20 或投资准备。本次未生成 JUnit XML/独立证据包；尚未做 O18 历史快照/基线全路径哈希对账，也未做进程强杀后 campaign 台账的独立灾难恢复演练。

实施后补充与 O01–O18 对应的实际测试节点、JUnit、命令、环境和输出哈希。必须检查 skipped/xfail/未收集项，不以退出码 0 代替覆盖核对。Docker 不可用属于 blocked，不是跳过后通过。L3/L4 不混入普通 pytest，也不提供当前不存在的启动命令。

## 11. 实施顺序、交付物与回滚

| 批次 | 改动与责任角色 | 放行条件 | 后续允许动作 |
| --- | --- | --- | --- |
| A：正确性 | 工程开发 E01–E03；研究负责人确认阈值口径 | O01–O10、O18；核心回归；输入/输出身份及历史保全验证 | 继续离线风险/统计诊断，不自动开始研究 |
| B：可信归因与审计 | 工程开发 E04–E05；研究负责人审核日级归因及多重试验方案 | O11–O15、O17–O18；无种子误标、无未知成本归零 | 冻结搜索与测试策略 |
| C：搜索改进 | 工程/研究协作 E06，衔接文档 15 W05–W06 | O16–O18；机制、初筛、配额与停止条件可执行 | 提交有限真实验证申请 |
| D：真实验证 | 研究负责人确认授权与预算；验收者独立检查 | O19；再按新 campaign 执行 O20 | 报告研究结果，必要时进入独立确认 |

每批单独可审阅变更，不同时重构记忆、模型和组合。最小上线以 A/B 的正确性为先；未完成复杂检索不阻止核心修复验收，但完整 campaign 放行必须满足下节清单。

每批拟议证据包位于**新的**批准输出目录，至少含：

- `manifest.json`：代码实际文件哈希、配置/协议/快照/基线身份、环境、镜像、用例版本；脏工作区不能只记 HEAD。
- `requirements_to_tests.json`：F01–F07 → E01–E06 → O01–O20 → 实际测试节点/结果。
- `test_results.xml` 与原始执行输出：通过、失败、跳过、未执行分别记录。
- `protocol_completeness.json`、`prompt_delivery_audit.json`、`evidence_verification.json`：只在完成相应验证后生成，不写空 PASS。
- `historical_integrity.json`、`campaign_ledger_audit.json`：历史原件对账、跨运行计数及覆盖。
- `release_decision.md`：工程状态、研究状态、未解决项、批准人角色、允许执行范围。

上述文件名是拟议交付契约，当前尚未生成。回滚仅回退本批代码/配置选择与派生视图指针，不删除失败产物，不修改旧协议，不将新身份工件混入旧 checkpoint。若发现完整性问题，停止新分派并保留现场；遇到仍存活的旧进程，先确认归属与退出情况，不停止或重启它来方便发布。

## 12. 恢复研究前的硬检查清单

- [ ] 正式压力收益阈值的单位、指标定义、数值及理由预先确认；新协议完整且通过 accepted 可达性测试。
- [ ] 最新经济失败实际送达下一 hypothesis/proposal 请求，五折和失败码保真，字节与 token 预算均通过。
- [ ] 模型根引用修复并通过反向安全测试；同身份有效证据可复用，缺证据失败原因可见。
- [ ] 风险日级归因完成；如改变策略，已有单独批准的新协议和匹配基线，不能复用不匹配旧回测。
- [ ] 全局搜索计数、model/bootstrap seed、未知费用语义完整；开发选择偏差限制显式报告。
- [ ] 候选数、调用数、修复数、时限、无进展停止条件与未知费用政策已冻结，不恢复无限搜索。
- [ ] 留出独立性经过检查，Agent 输入与检索工件没有留出泄漏；数据资格未通过不得标投资准备完成。
- [ ] 必需离线场景通过且有真实生命周期集成证据；历史快照、基线、报告和 checkpoint 哈希不变。
- [ ] 用户明确批准新的真实实验范围；另建 campaign/run，不继续写入已结束的 r25。

待决策项：成本压力阈值的业务数值；是否只诊断风险或批准新风险执行版本；新 campaign 的有限上限；独立验证窗口及选择偏差控制方法。它们不阻止先完成通用离线修复，但阻止未经确认的正式研究放行。

最终判断标准不是“运行次数更多”或“终于出现一个漂亮收益”，而是：系统能完整吸收失败、正确复用证据，并在预先冻结、可审计的规则下得到可复现的 accepted；若没有，则同样准确地报告没有找到。
