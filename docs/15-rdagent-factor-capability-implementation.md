# RDAgent 因子提出能力与去重优化：实施及验证方案

日期：2026-09-22。版本：设计 v1.0。状态：**待实施、待验收**。

本文件交付工程规格，不代表下列功能已经实现或测试通过。2026-09-22 本次工作只读取代码、核查已有报告并编写文档，没有启动真实 LLM、训练或回测。

目标：提高固定研究预算内提出“可检验、非重复、具有条件增量”的因子的能力；避免同一研究问题被反复编码、训练和回测；保留全部失败与搜索历史。允许最终没有因子入选。

关联：[优化总方案](10-llm-factor-optimization.md)、[后续路线](12-llm-factor-optimization-next.md)、[既有工程规格](13-llm-factor-engineering.md)、[既有测试标准](14-llm-factor-test-standard.md)。本文件细化下一批交付；历史版本、协议和运行产物不被本文追溯修改。

## 1. 交付边界与有效性定义

### 1.1 四层结果分开报告

| 层级 | 定义 | 可以声称 | 不能声称 |
| --- | --- | --- | --- |
| Q1 可检验提案 | 合法输入、具体机制、与已有特征的差异、可观察的否证条件齐全 | 提案合格 | 因子有预测能力 |
| Q2 正确实现 | 公式与代码一致，因果、索引、预热、缺失和全量质量检查通过 | 因子正确实现 | 因子经济有效 |
| Q3 开发期增量 | 在冻结模型/数据/成本/执行规则下通过配对、稳定性与消融门槛 | 当前基线和开发期下具有增量 | 泛化成立、可投资 |
| Q4 独立确认 | 冻结候选后在未参与搜索的合格区间按原门槛确认 | 独立确认结果 | 自动达到实盘/投资批准 |

系统优化分别验收工程正确性、资源效率、提案质量和研究增量。单因子 IC、LLM 自评分、一次回测收益、合成 PASS 都不能单独定义有效因子。

### 1.2 本阶段固定的边界

- 原始数据 `D:\qlib_data\etf_qlib_data` 只读；测试使用临时合成快照，实际研究引用已审核的不可变快照。
- 不自动改变已冻结的模型、标签、折、种子、标的池、交易频率、佣金/滑点、风险和入选门槛。实际数值从待测协议读取，不从旧说明或示例复制。
- 现有进程、旧 checkpoint 和历史研究产物不修改；代码变更使用新运行身份。
- 最终留出数据及其统计、回测结果不能进入生成器、研究记忆或提示词调优。
- 本阶段不建设新的多智能体服务、向量数据库或在线强化学习；先扩展现有 Python 模块、本地索引、FileLock 和 RunStore。
- 受支持表达式的规范化先行；通用表达式编译器、模型路由和调用合并为后续独立工作包。

## 2. 当前核查证据与问题定位

核查工作区 HEAD：`b0522ad`，但工作区已有修改和未跟踪实现，**该提交号不能独立复现当前代码**。以下结论依据实际文件内容；实施前重新确认差异。

| 编号 | 当前行为与定位 | 风险/缺口 | 对应工作包 |
| --- | --- | --- | --- |
| B01 | `first_loop.execute_first_loop` 创建 ResearchSession 时未传 memory_root；session 默认 root.parent | 首轮历史检索范围停留在当前 run；普通 research CLI 与首轮入口不一致 | W01 |
| B02 | `research_memory.exact_duplicate` 去空白后比公式，返回第一个匹配 | 忽略完整定义语义；首条不可比/技术失败可能遮蔽后续可比结论 | W03 |
| B03 | `proposal.convert` 将重复产生的 QualityError 纳入 proposal_repair | 确定重复可能再次发起模型调用 | W02 |
| B04 | controller 的异常 trial 不保存完整 hypothesis/proposals/research_card | 失败计入次数但难以被下一轮检索利用，实际阶段也容易丢失 | W01 |
| B05 | controller 先 promote_trial，再用 session 当前基线和协议建卡 | 成功入选路径存在评估前/后的身份混淆，应由回归用例验证并修正 | W01 |
| B06 | 成功路径在新 checkpoint 落盘前 rebuild 索引并写 search_ledger | 快照计数可能落后本轮；失败路径也未统一刷新台账 | W01/W04 |
| B07 | memory_index.load 每次全量 rebuild，且只扫描 root 与一层含 sessions 的目录 | 不能靠简单扩大 root 就覆盖所有嵌套运行；规模增大后重复 I/O | W01/W04 |
| B08 | 已有 FeatureArtifact、配对子运行/辅助回测缓存和单因子消融复用 | 应补齐跨运行依赖与入场控制，不重写另一套回测缓存 | W05 |

代码入口：[记忆索引](../src/etf_ml/research/memory_index.py)、[公式匹配](../src/etf_ml/research/research_memory.py)、[提案](../src/etf_ml/adapters/rdagent/proposal.py)、[控制器](../src/etf_ml/research/controller.py)、[会话](../src/etf_ml/research/session.py)、[首轮](../src/etf_ml/research/first_loop.py)、[配对](../src/etf_ml/research/paired.py)。

读取时 `tushare-formal-first-loop-20260922-r10/first_loop_report.json` 为 completed，formal_g0_passed、holdout_evaluated、investment_accepted 均为 false；其候选配对结果为 rejected。这里仅记录报告字段，不据此判断所有数据治理工作的最新状态，也不把技术闭环当作投资验收。

核查文件 SHA-256：

```text
controller.py       8ff1cf7a67a9ba6a14eeafa5d8908c65c48d138149fb5a03ad596ba72b56e77b
memory_index.py     254be5eef7b8f8fdf034f17d171bbea888be63ccc10e886157eca05b18a98229
research_memory.py  3a9ae7f062b70daaad96ffcd4b4d80ab24b4892a9aa0f1d669e3a119b697529b
proposal.py         d06e94cf4dae5b8e6846227f58e9553e8783244b9f98bfc346cebe8b91194278
first_loop.py       206cb38aaa0ed3444f17992508eb2d6627e579e09cdffb75c79b51c35e908232
```

## 3. 目标流程与模块分工

```text
冻结研究身份和搜索政策
  → 检索项目历史、构造研究任务
  → 生成假设/提案、提交阶段证据
  → 校验完整定义、查询完整去重索引
      → reuse：核验并引用原结论
      → wait_existing：已有同身份计算，退出或等待其终态
      → blocked：证据冲突/损坏，不自动重跑掩盖问题
      → repair：同定义有限技术修复
      → retest/new：登记依据和额度
  → 编码、小样本公式/因果检查
  → 全量材料化和质量检查
  → 固定协议配对、成本压力及消融
  → 写入评估结论和入选后的基线身份
  → 原子提交 trial/checkpoint，再更新派生索引
  → 生成证据卡片、更新搜索台账、判定继续或停止
```

硬去重读取全量可信索引；给 LLM 的几张历史卡片只用于引导生成，不承担硬去重。候选筛选权继续属于可信评估程序，LLM 只提出假设和实现。

| 模块 | 实施内容 | 不承担的责任 |
| --- | --- | --- |
| `research/session.py`、`first_loop.py`、`cli.py` | 统一项目记忆路径，绑定评估前后身份、搜索政策 | 不在读取旧会话时静默升级身份 |
| `research/memory_index.py` | 显式来源登记、索引完整性、精确查询和相关检索分离 | 不凭公式文本合并不同协议收益 |
| `research/research_memory.py` | v2 卡片、成功/失败/复用摘要、稳定引用 | 不用 LLM 总结或补造缺失证据 |
| 拟新增 `research/factor_identity.py` | 完整定义投影、受限 AST 规范化、身份生成 | 不证明任意 Python 程序等价 |
| 拟新增 `research/search_policy.py` | 准入结果、配额、重测理由和停止规则 | 不修改经济入选门槛 |
| `adapters/rdagent/proposal.py`、`prompting.py` | 机制任务、证据引用、去重决策路由 | 不把经济失败当格式修复 |
| `research/controller.py` | 状态提交、失败归档、入场锁、恢复和调用计数 | 不重算已有完整结论来刷新统计 |
| `factor_engine.py`、`factor_worker.py`、`paired.py` | 分级检查、缓存核验、阶段耗时 | 不另建收益/费用口径 |
| `contracts.py`、`protocol.py` | 新版本严格契约和冻结政策 | 不以默认字段改变旧协议哈希 |

## 4. 数据契约和接口

以下类型、字段及接口均为拟议设计；实现时与严格 schema、序列化和 CLI 同步，不可直接当作当前 API 使用。

### 4.1 因子、实现和实验身份

| 身份 | 哈希输入 | 排除/约束 |
| --- | --- | --- |
| definition_id | identity_schema、canonicalizer_version、规范表达式、参数、字段语义/单位/复权版本、lookback、minimum_observations、available_at、missing_policy、横截面及分组语义、适用范围 | 展示名称、自由文本理由不参与；任何影响数值/信息时点的字段不能排除 |
| implementation_id | definition_id、源码哈希、算子/依赖版本、运行镜像和数值环境 | 同定义的代码修复产生新实现，不重置经济候选数 |
| evaluation_id | implementation_id、输出哈希、snapshot、评估前 baseline、protocol、模型/特征顺序/预处理、折/种子、成本/执行政策、评估依赖及选择规则 | P1 保留现有完整 protocol_id/source_code_hash，不提前收窄依赖 |
| admission_id | definition_id、当前快照、评估前基线、完整 protocol_id、检查/实现政策版本 | 编码前使用；解决 implementation_id 尚不存在时的重复入场 |

admission_id 不是经济结论证据。它只用于找到历史完整实现/评估或识别在途任务。命中后仍校验实现、所有工件和当前兼容性。技术失败不能永久占用 admission_id。

生成来源单独保存模型/提供商配置指纹、prompt/schema/projection 版本和 request/response 哈希。跨提示词版本复用只能在后续精确依赖审查通过后启用，不能因理论上经济设置相同就绕过现有协议检查。

### 4.2 ResearchCard v2

必需字段分组：

```text
schema_version, project_id, run_id, trial_id, definition_id_or_null
hypothesis, proposal_or_null, mechanism_family, parent_trial_id_or_null
evaluation_snapshot_id, evaluation_baseline_id, evaluation_protocol_id
resulting_baseline_id, resulting_protocol_id
stage, attempt_outcome, economic_status_or_null, failure_category_or_null
decision_reasons, evidence_refs[{artifact_id, sha256}], qualification
implementation_id_or_null, evaluation_id_or_null, reuse_of_or_null
changed_dimension_or_null, retest_reason_or_null, summary, card_hash
```

`economic_status` 仅在实际完成评估或引用完整已验证评估时赋值。格式失败、未运行、复用决策都不能伪装成新的 accepted/rejected 经济实验。缺失定义的失败仍有 trial_id，但 definition_id 为 null。

`card_hash` 覆盖白名单卡片内容，排除自身哈希字段；trial 的实际文件哈希由 checkpoint 绑定。路径通过本地 artifact_id 解析；提示词只接收投影后的证据引用，不包含主机路径、原始数据行、密钥或留出内容。

旧卡片缺关键语义时标记 `legacy_identity_incomplete`，可检索但不能作为语义硬去重证据。仅在原 spec、trial 和 checkpoint 足以恢复身份且校验通过时，生成带来源的新索引视图；不修改旧文件。

### 4.3 DedupDecision

```text
decision: new | reuse | repair | retest | wait_existing | blocked
definition_id, admission_id
matched_trial_ids[], reusable_evaluation_id_or_null
reason_codes[], equivalence_basis, compatibility_report
retest_reason_or_null, evidence_refs[], decision_hash
```

建议接口：

```python
identify_definition(spec, field_catalog, canonicalizer_version) -> FactorIdentity
find_exact(identity, evaluation_context) -> list[VerifiedResearchRecord]
decide_admission(identity, matches, search_policy) -> DedupDecision
retrieve_related(task, budget) -> list[ResearchCard]
commit_attempt(attempt_record, checkpoint) -> CommitReceipt
```

`find_exact` 不能取第一条后停止：先聚合全部匹配，优先核验同评估身份的完整结论；同身份结论冲突返回 blocked。异基线历史只提供解释，不能遮蔽当前基线下已存在的结论。

### 4.4 规范化范围

第一批仅支持审核过的算术表达式与 shift/rolling_mean/rolling_std 等算子，显式绑定轴、窗口、min_periods、ddof、缺失和除零规则。AST 只做解析，不执行 eval。

- 可合并：空白、无语义括号、经审核的字段别名和确实等价的算子拼写。
- 不自动合并：浮点结合律改写、参数近邻、正负号变换、rank/scale 变换、不同缺失规则、分母为零时不等价的表达式。
- 解析不支持时标记 opaque，只做完整原始定义的保守精确匹配；不让 LLM 宣称等价后硬拒绝。
- 数值相同仅证明对应快照上的输出一致，不证明任意未来数据上等价。有限探针、高相关或完全相关都不能单独成为通用定义等价证明。

### 4.5 搜索台账

采用独立只追加事件和可重建聚合视图，先复用原子 JSON 文件及 OS FileLock。每条事件有 schema_version、稳定 event_id、project/run/trial、事件种类、来源哈希和提交序号；重放相同 event_id 不重复记数。

分别统计：generation_attempts、valid_proposals、unique_definitions、family_variants、technical_repairs、physical_llm_attempts、duplicate_proposals、completed_unique_evaluations、reused_evaluations、accepted_unique_definitions、inconclusive_evaluations。历史缺项显示 unknown/coverage，不能由 trial_count 倒推出所有调用。

开始调用前写 request intent；调用结果与已有账本绑定。发送后中断且结果未知时保留 uncertain，不自动认为未收费或安全重发。未提交经济工件可计已发生尝试，不能贡献完成/入选数。

## 5. 实施工作包

### W01 / P0：项目记忆、身份与提交顺序

1. 各新入口显式使用 `<artifact_root>/research_memory/v2`。登记真实会话目录与来源资格，不递归扫描整个 artifacts。
2. 为历史 research、首轮 runs/*/research、diagnostic_first_loop/runs/*/research 建立可审核的来源清单；只导入哈希有效的已提交证据。不同 artifact_root 需显式登记，不能自动跨项目合并。
3. trial 开始保存评估前快照/基线/协议；卡片在入选前构造评估部分，入选后追加 resulting_*。
4. hypothesis 成功返回后立即保存；proposal 校验阶段也保存结构化错误和原响应哈希，确保异常归档能拿到已有证据。
5. 统一成功、拒绝、失败、复用的提交函数：写 trial → 写 checkpoint → 刷新派生索引/台账。索引失败则标记 stale 并阻止下一次依据不完整历史生成，不能再次执行已提交 trial。
6. 恢复时先对账已提交 trial 与事件，按 event_id 补缺；不重复计数、调用或提升基线。持久化本轮检索视图/卡片选择身份，其他 run 新增历史不能改变在途 prompt。

交付：v2 卡片/来源清单及读取器、入口接线、提交/恢复路径、失败码。验收：D01–D08、D38。

### W02 / P0：重复与修复分流

1. 把提案结构/语义校验与去重准入拆开；只有允许的格式/语义错误进入 proposal_repair。
2. 重复返回 DedupDecision，不再作为通用 QualityError 被修复捕获；进入下一提案须作为新 generation_attempt 计数。
3. 区分 schema_error、implementation_error、data_contract_failure、causal_violation、economic_rejected、duplicate、transport_uncertain。恢复/修复规则由程序判定。
4. 技术修复必须绑定原 definition_id；修改窗口、公式或缺失语义就是新候选。被修复实现重新通过相关质量门。

交付：明确分支、控制器兼容状态和 CLI 可解释终态。验收：D09–D11、D26。

### W03 / P1：完整定义去重与有限规范化

1. 在 factor_identity.py 实现纯函数定义投影，字段目录缺必要语义时返回不可硬判定。
2. 先接完整定义精确匹配，再逐个加入有独立 oracle 的规范化规则。每条规则有版本、正例与反例。
3. 精确查询返回全量历史匹配；相关检索保持预算限制。已有基线特征目录也参与重叠诊断，只有数值语义经验证的精确重复才可拒绝。
4. 为变体登记机制族、参数变化和父试验；不得用数值相关阈值替代语义身份。

交付：三层身份、admission_id、DedupDecision、旧记录兼容适配。验收：D12–D20。

### W04 / P1：在途互斥与完整搜索计数

1. 同 admission_id 在本地单机取得 OS 锁，持锁后重新查询已提交结果，再决定恢复/执行；锁覆盖该次计算，网络调用不在项目全局索引锁内。
2. 统一锁顺序：会话锁 → admission 锁 → 短时索引/台账锁。任何代码不得逆序取得锁；进程终止由 OS 释放锁，不凭锁文件年龄删除活跃锁。
3. 竞争者返回 wait_existing；无有效终态但前持有者已退出时，核验 checkpoint 后恢复同一工作，或登记显式重试。未确认 LLM 发送按 uncertain 处理。
4. 项目事件聚合不因换 run 清零；本轮窗口/机制配额与项目累计统计分别保存。
5. 首版索引可全量重建以保证正确；测量规模瓶颈后增加按 checkpoint 提交身份的增量索引。不得缓存后跳过复用时工件核验。

交付：入场互斥、只追加事件、重建工具接口和计数报告。验收：D21–D25、D35。

### W05 / P1：分级检查与计算复用

| 阶段 | 检查/输入 | 允许的提前终止 | 输出证据 |
| --- | --- | --- | --- |
| S0 运行预检 | Docker、镜像、数据资格、预算和 replay 阶段 | 缺必要资源/契约则停止，付费调用为零 | preflight |
| S1 定义与去重 | 合法字段、完整定义、历史/基线索引 | 不合法、确定重复、证据冲突 | proposal、dedup_decision |
| S2 小样本实现 | 人工可算多 ETF 面板、预热、缺失、公式/时点 oracle | 实现失败或明确因果违规 | quality_checks、repair_history |
| S3 全量质量 | 开发面板材料化、覆盖、非有限值、重复键 | 违反原质量契约 | factor manifest、quality report |
| S4 条件增量 | 原协议五折/种子矩阵、净成本、消融和诊断 | 按原经济门槛判断；不得只挑好折 | paired reports、完整收益工件 |
| S5 归档 | 基线提升、卡片、checkpoint、台账 | 完整性失败则停止下一轮 | trial、commit receipt |

S2 不根据小样本收益淘汰；S3 的相关性只作解释/排序，不能硬淘汰可能有模型增量的因子。S4 使用待测协议的真实折和种子，表中“五折”是现有研究布局，合成测试可用明确标注的简化布局。

先复用已有完整 FeatureArtifact、基线和辅助缓存；当数据/定义/实现不变而评估基线变化时，可以复用已验证因子输出，但必须重新评估条件增量。跨源码/提示词协议复用暂不开放。

记录每级 wall time、CPU time（可测时）、材料化/训练/回测次数、缓存核验耗时、峰值内存及淘汰原因。冷/热缓存分开报告。

交付：阶段化事件、复用引用和调用计数；验收：D27–D30、D36。

### W06 / P2：机制驱动生成与有界调度

每轮生成 ResearchTask，含机制族、已有特征差异、相关成功/失败、尚未解决的问题、允许改变的一个维度和停止条件。提案引用 evidence_ids，由程序检查对应原工件内容；不接受不存在的“历史证明”。

拟议首批搜索参数（需新 schema 与冻结配置，不是可直接粘贴的现有 YAML）：

| 参数 | 建议起始值/规则 |
| --- | --- |
| maximum_unique_definitions | 6；3 个独立机制槽位，最多 3 个证据驱动变体槽位 |
| maximum_generation_attempts | 12；重复/无效输出也占用，修复另计物理调用和修复额度 |
| maximum_variants_per_parent | 1；每次只改一个预声明语义维度 |
| maximum_consecutive_duplicate_proposals | 3；达到即停止本轮并报告停滞 |
| repairs | 沿用或显式冻结新上限；不得因增加搜索轮次隐式扩大 |
| physical call/cost/time limits | 从新运行政策显式冻结；unlimited 金额不代表候选与时间无限 |

技术修复不占“新经济定义”额度，但占修复与资源额度；经济定义修改必占新定义槽位。调度优先选择未覆盖且有数据支持的机制，不使用当前最高回测收益作为唯一奖励。无有据可检验的新假设可提前结束。

交付：ResearchTask、证据引用验证、确定性配额调度。验收：D31–D34、D37。

### W07 / P3：有限真实生成对照与独立确认

先完成 W01–W06 的离线验收，再冻结对照集。A/B 初始使用 6 个代表上下文，每组同模型/生成参数、相同经济/数据条件与调用上限，交错运行；上下文包含无历史、已拒绝、技术失败、参数近邻、已累积基线、不可比历史。

第一轮固定相同历史卡片，评价提案策略；第二轮单独评价多轮闭环，让各策略生成自己的历史，使用相同起点、机制覆盖目标和预算。不能混用两种实验解释收益。A/B 提示词不同允许不同协议 ID，比较按 case_id 与冻结的经济配置对齐，不伪造相同 protocol_id。

预先固定完整回测样本选择：优先全部唯一且合格提案；预算不足则在看收益前按固定排序/种子抽取，记录纳入概率与未评估数。不得选择 IC 最好的提案才算生成效果。

对照只改变一个策略；计算缓存改进和生成策略改进分两轮测。6 个案例是小规模起点，只报告逐例差异和不确定性，不宣称统计上普遍提升。独立确认候选及区间先冻结；用来选择提示词的区间视为已使用。真实数据资格不完整时只能给诊断结论。

交付：A/B manifest、全部请求及失败、盲评、成本/计算分解、开发期与独立确认报告。验收：D39–D42。

## 6. 固定测试矩阵

以下 42 个验收场景均为**计划用例**，不表示已有同名测试或已通过。可以扩展现有测试文件；不得把 pending/skip 算作 PASS。

### 6.1 P0 正确性

| ID | 构造/操作 | 必须断言 |
| --- | --- | --- |
| D01 | CLI research 与 first-loop 各写一条已提交历史，另起会话 | 两个入口都检索两条，不依赖启动 cwd |
| D02 | 登记诊断/正式来源，混入未登记目录和越界路径 | 只读登记来源，资格可见，越界拒绝 |
| D03 | checkpoint 未引用 trial、trial 缺失或哈希被改写 | 孤儿不入经济索引，已提交缺失/篡改显式报错 |
| D04 | 构造明确 accepted，baseline B0 提升到 B1 | 卡片评估身份为 B0/P0，结果身份为 B1/P1 |
| D05 | hypothesis 后或 proposal 校验中抛错 | 已有假设/响应证据和真实失败阶段保留，经济状态为 null |
| D06 | 提交最后一个成功或失败 trial 后停止 | 索引与已提交次数一致，无“最后一轮少一条” |
| D07 | 在 trial 写完/checkpoint 前、checkpoint 后/index 前分别中断 | 前者不形成经济结论；后者恢复仅补索引，调用/训练不增加 |
| D08 | 旧 v1 卡片缺 missing_policy/可用时点，读写新索引 | 原文件哈希不变，标记不完整，不参与语义硬判定 |
| D09 | 模型返回已完成同身份定义，注入计数传输/coder/runner | hypothesis/proposal 可已发生；proposal_repair、code 和训练新增次数为 0 |
| D10 | 首次提案包络错误，修复后合法 | 仅允许的格式修复发生，修复额度与物理调用正确计数 |
| D11 | 先遇到不可比/技术失败匹配，再遇到当前身份完整结果 | 不被第一条遮蔽；排列输入顺序不改变决策 |

### 6.2 身份与等价边界

| ID | 构造/操作 | 必须断言 |
| --- | --- | --- |
| D12 | 同完整定义更名、空白和冗余括号变化 | 受支持语法下 definition_id 相同 |
| D13 | 同名但公式、字段或参数变化 | definition_id 不同 |
| D14 | 相同公式改 min_periods、ddof、缺失/除零策略 | definition_id 不同 |
| D15 | 相同数值字段名改单位、复权、available_at、横截面轴 | 不合并；未知语义不可硬判定 |
| D16 | rolling 20 改 21；乘 -1；增加 rank | 标变体/新定义，不声称等价 |
| D17 | x/x 与常数 1，输入含 0、NaN；浮点结合顺序变化 | 不使用不安全代数约简 |
| D18 | opaque 自由文本定义或未支持算子 | 保守原始定义匹配，标明支持边界，不崩溃或猜测 |
| D19 | 同定义技术修复，或同输出换数据/基线/成本/种子/依赖 | 实现变化相应换实现 ID；评估依赖变化换评估 ID |
| D20 | 规范化版本变化和旧协议加载 | 新身份不冒充旧身份，旧协议内容/哈希保持 |

### 6.3 台账、并发、计算与搜索

| ID | 构造/操作 | 必须断言 |
| --- | --- | --- |
| D21 | 两个独立进程用 barrier 同时提交同 admission_id | 一次编码/评估，另一个 wait/reuse；无死锁 |
| D22 | 锁持有者退出，分别有完整工件/半成品 | OS 释放锁；完整者核验复用，半成品按 checkpoint 恢复 |
| D23 | 重放相同事件；相同候选换 run；新增真实提案 | event_id 幂等，项目累计不清零，新尝试照计 |
| D24 | 发送后连接断开，无可核验服务商用量 | uncertain 保留，不当零费用、不自动重发 |
| D25 | 同一 evaluation_id 注入互相矛盾的已提交结果 | blocked，不能取最新或最优值 |
| D26 | 语法错误修复与 no_increment/因果违规分别回放 | 只修可恢复实现；改定义占新候选额度；经济拒绝不触发修复 |
| D27 | 3 ETF × 8 日人工面板，含 NaN、零、常数和打乱顺序 | 独立 oracle 验证 shift(2)+rolling(3) 最早第 5 行有效，无跨 ETF 污染 |
| D28 | 重复提交完整已验证因子及配对实验 | 新材料化/训练/回测数为 0；返回工件哈希与原结果一致 |
| D29 | 删除任一缓存 manifest、结果、基准/压力日收益文件或改 hash | 不命中；按规则重算或显式阻止，不静默接受 |
| D30 | 同因子换评估基线，因子输入/实现仍相同 | 可复用因子输出，必须重新评估增量；旧入选结论不直接沿用 |
| D31 | 生成 3 次连续重复，或尝试/定义/资源额度达到上限 | 冻结规则触发停止，无隐式补偿重试 |
| D32 | 变体有/无 parent_trial，引用不存在的失败证据 | 无依据变体拒绝；合法变体只改一个预声明维度 |
| D33 | 技术失败、重复、复用与真实新候选混合 | 各计数分开且总量可从事件重建，不按卡片数冒充提案数 |
| D34 | 低 IC/高相关但合法且可能有交互增量的夹具 | S2/S3 不经济硬拒绝；完整评估仍使用原门禁 |
| D35 | 原始卡片/嵌套摘要注入留出、路径、密钥和指令文本 | 白名单投影拒绝/去除敏感项，原文不成为控制指令 |
| D36 | 同一冻结重放集冷/热缓存各运行 3 次 | 分开报告中位耗时和物理计算数，数值/决策一致；不把缓存核验算训练 |
| D37 | 第一轮正常 rejected，第二轮提出引用该结论的修正 | 真实发送 prompt 含失败原因/差异要求，无留出；伪造引用拒绝 |
| D38 | 会话 A 在途时会话 B 提交新历史，然后恢复 A | A 使用已绑定视图和同一 prompt/额度，不因全局索引变化失去可恢复性 |

### 6.4 真实生成与确认

| ID | 方法 | 必须报告/断言 |
| --- | --- | --- |
| D39 | 6 上下文 A/B 交错生成，固定模型/参数与预算 | 全部输出留存，逐例合法/唯一/可执行结果，失败保留在分母 |
| D40 | 隐去策略名后盲评机制、差异、时点、忠实度、结论边界，各 0/1/2 | 关键时点或实现错误直接失败；逐例评分及分歧，不仅总均分 |
| D41 | 按事前规则进入完整配对，全部折/种子及消融 | 提案优化不改变比较协议经济字段；报告有效候选数而非最好一次收益 |
| D42 | 候选冻结后单独确认；确认期结果不回灌当前搜索 | 区间使用历史完整；样本不足或数据资格不足标 inconclusive/diagnostic |

## 7. 测试实现、命令与证据

### 7.1 测试文件布局

已有可扩展：`tests/unit/test_llm_factor_v1.py`、`test_v2_diagnostics.py`、`test_research_code_repair.py`、`test_budget_llm.py`、`test_first_loop_reuse.py`、`test_auxiliary_cache.py`，以及 `tests/integration/test_rdagent_research.py`、`test_paired_research.py`、`test_accumulated_research.py`。

拟新增（本次不创建、不宣称存在）：

| 文件 | 场景 |
| --- | --- |
| `tests/unit/test_research_memory_v2.py` | D01–D08、D11、D35、D38 |
| `tests/unit/test_factor_identity.py` | D12–D20 |
| `tests/unit/test_factor_admission.py` | D09–D11、D25–D26 |
| `tests/unit/test_search_policy.py` | D23–D24、D31–D34 |
| `tests/integration/test_research_dedup_recovery.py` | D07、D21–D22、D28–D30、D37–D38 |
| `tests/fixtures/factor_optimization_v2/manifest.json` | 冻结案例、输入/期望/规范化规则哈希及独立 oracle 来源 |

单元测试用计数 transport、coder/runner spy 验证“没调用”，不能仅断言 status。经济 fixture 的 expected 由手算或独立实现提供，不能调用被测规范化器/候选代码生成期望值。真实集成不 mock Qlib、容器、RunStore 或工件核验；用 ReplayTransport 替换付费模型。

并发用 multiprocessing spawn 和显式同步点，测试子进程终止及超时退出；不得对真实研究 PID 做故障注入。禁止静默联网或使用生产数据目录作为 pytest 临时目录。

### 7.2 已有回归命令

以下命令是实施者可运行的验证步骤，**本次文档交付未执行这些测试**。在项目根目录 PowerShell 中执行；使用独立报告目录，保留 JUnit、pytest 输出、环境和冻结身份。前置确认当前无需保护的同源码运行；每阶段只运行其相关测试。

```powershell
Set-Location -LiteralPath 'D:\quant_project\ETF-Qlib'
$factorOptPython = 'E:\tools\anaconda\envs\qlib_zhengshi\python.exe'
$factorOptEvidence = Join-Path (Get-Location) ('artifacts/tests/factor-capability-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $factorOptEvidence | Out-Null
$factorOptUnits = @(
    'tests/unit/test_llm_factor_v1.py',
    'tests/unit/test_v2_diagnostics.py',
    'tests/unit/test_research_code_repair.py',
    'tests/unit/test_research_code.py',
    'tests/unit/test_budget_llm.py',
    'tests/unit/test_development_feedback.py',
    'tests/unit/test_first_loop_reuse.py',
    'tests/unit/test_auxiliary_cache.py',
    'tests/unit/test_research_runtime_preflight.py'
)
& $factorOptPython -m pytest @factorOptUnits -q ('--junitxml=' + (Join-Path $factorOptEvidence 'existing-unit.xml'))
if ($LASTEXITCODE -ne 0) { throw 'Existing unit regression failed' }
```

新增测试文件完成后，单独执行：

```powershell
# 以下文件尚待实施；缺失时应停止，不能用 skip 冒充验收。
$factorOptNewUnits = @(
    'tests/unit/test_research_memory_v2.py',
    'tests/unit/test_factor_identity.py',
    'tests/unit/test_factor_admission.py',
    'tests/unit/test_search_policy.py'
)
foreach ($factorOptTestFile in $factorOptNewUnits) {
    if (-not (Test-Path -LiteralPath $factorOptTestFile)) { throw "Pending test file: $factorOptTestFile" }
}
& $factorOptPython -m pytest @factorOptNewUnits -q ('--junitxml=' + (Join-Path $factorOptEvidence 'new-unit.xml'))
if ($LASTEXITCODE -ne 0) { throw 'New unit acceptance failed' }
```

固定 Docker 镜像和 Qlib 运行时可用后执行真实隔离回放；该步骤必须保持零付费外部模型调用：

```powershell
$factorOptIntegration = @(
    'tests/integration/test_factor_container.py',
    'tests/integration/test_rdagent_research.py',
    'tests/integration/test_paired_research.py',
    'tests/integration/test_accumulated_research.py'
)
& $factorOptPython -m pytest @factorOptIntegration -q ('--junitxml=' + (Join-Path $factorOptEvidence 'existing-integration.xml'))
if ($LASTEXITCODE -ne 0) { throw 'Existing integration regression failed' }
# 仅在新增文件完成后执行：
& $factorOptPython -m pytest tests/integration/test_research_dedup_recovery.py -q ('--junitxml=' + (Join-Path $factorOptEvidence 'dedup-recovery.xml'))
if ($LASTEXITCODE -ne 0) { throw 'Dedup/recovery integration failed' }
git -c safe.directory=D:/quant_project/ETF-Qlib diff --check
if ($LASTEXITCODE -ne 0) { throw 'Whitespace validation failed' }
```

命令退出 0 不代表所有必需场景通过：必须读取 JUnit，逐项对应 D01–D38，检查 skipped/xfail/未收集项。镜像不可用应报告 blocked，而不是把容器测试跳过后批准发布。D39–D42 另用显式真实研究入口和冻结 manifest，不把 live 调用混入普通 pytest；相应对照驱动脚本尚待 W07 实施，不提供虚构可执行命令。

### 7.3 指标和通过条件

| 指标 | 定义 | 门槛/解释 |
| --- | --- | --- |
| 精确去重召回率 | 正确拦截的确定重复 / 固定集内全部确定重复 | 固定集 100%，不外推无限表达式 |
| 错误拦截数 | 非等价、允许修复或合法重测被硬拦截数 | 固定集 0 |
| 完整复用新增计算 | 命中后新增 code/materialize/train/backtest 次数 | 对应层完整复用均为 0；上层变化按依赖重算 |
| 唯一合法提案率 | 唯一合法完整定义 / 全部生成尝试 | 对照报告；重复/无效保留分母 |
| 首次实现率 | 首次代码通过 Q2 的定义数 / 已进入编码的唯一定义数 | 同时报告全部生成尝试口径，避免筛选掩盖 |
| 开发期有效产出 | 固定预算内通过 Q3 的唯一因子数 | 不预先保证非零；重测/复用不重复计成果 |
| 每有效因子成本 | 本组全链路成本 / Q3 唯一因子数 | 分母 0 为 undefined；未知账单为 unknown |
| 用量完整性 | 有可核验完整 usage 的物理尝试 / 全部物理尝试 | 未知请求不从分母删除，估算与真实值分栏 |
| 研究偏差记录 | 所有生成/变体/修复/筛选/区间使用历史 | 新 run 不重置，种子不冒充独立市场样本 |

W01–W06 发布硬门槛：必需场景全部通过、去重零误杀、身份/因果/留出隔离零漏放、同身份恢复一致、计算复用证据完整。质量通过但未实现资源节省，标“正确性通过、效率未达标”。

W07 提案质量门槛：同固定案例下合法唯一数、首次实现数和盲评均不劣于对照，逐例关键错误为零；小样本只能作为试点门槛，不是总体不劣证明。研究有效性另报告 Q3/Q4，不以工程结果替代；无法区分改善与随机波动时保留 inconclusive。

### 7.4 证据包

每阶段输出独立目录，至少包含：

```text
manifest.json                 # 时间、代码/未提交差异/配置/环境/协议身份
case_manifest.json            # 场景及输入/期望哈希
commands.txt                  # 实际命令与退出码，无凭据
unit.xml / integration.xml    # 实际执行报告，缺项明确标记
case_results.json             # Dxx -> test node ID -> PASS/FAIL/PENDING/BLOCKED
calls_and_compute.json        # 全部物理调用及计算次数、复用引用
quality_and_cost.json         # 指标、分母、unknown、冷/热缓存
recovery_evidence.json        # 故障点及恢复前后哈希/计数
acceptance.md                 # 工程、效率、研究、未覆盖项分开结论
```

文件名是拟议验收产物，不要求没有相应验证的阶段生成空 PASS 文件。历史报告只作为输入锚点，不能计入本次通过数。

## 8. 实施顺序、发布与回滚

| 批次 | 工作包 | 出口条件 | 后续允许动作 |
| --- | --- | --- | --- |
| A 正确性修复 | W01 + W02 | P0 用例及现有相关单元/Replay 通过 | 接入完整定义去重 |
| B 去重与复用 | W03 + W04 + W05 | 身份、并发、恢复、缓存边界通过 | 开始机制与配额调度 |
| C 生成能力 | W06 | 多轮离线/真实容器 Replay，D01–D38 覆盖闭合 | 冻结真实对照预算和样本 |
| D 能力确认 | W07 | D39–D42 按资格执行并报告 | 依据证据决定扩大搜索或保留原策略 |

每批单独可审阅变更和验收报告；不以一个大重构同时修改索引、缓存、模型和策略。先完成 A，再 B；没有瓶颈计量，不增加复杂检索或改变模型路由。

上线先在独立测试 artifact_root 做只读影子准入判定，再在新研究身份下启用硬去重。影子阶段本身不发起生产训练/LLM，不为对比而重跑历史经济实验。两版决策有差异时查看定义、兼容性和证据，再批准对应范围。

回滚使用匹配旧代码/配置的新运行或原身份可恢复运行；保留新版证据但停止向旧 schema 写入。不能把新版 checkpoint/卡片覆盖进旧 run，也不能删除失败记录恢复好看的统计。出现身份冲突、缓存污染、调用重复、留出泄漏或误杀合法候选时停止新试验，修复并通过对应失败用例后再恢复。

## 9. 实施前待冻结事项

1. 本轮研究快照、基线、模型/提供商和费用/调用/时间上限：从实际新协议登记，不能从本文示例推断。
2. 首批受支持算子、规范化规则与 oracle：W03 交付前锁定；不支持的表达式走保守路径。
3. 真实 A/B 案例清单、盲评方法、完整回测抽样和确认区间：在读取相应结果前冻结。

以上不阻止先实施零付费的 A/B/C 工程批次；真实实验按当时有效的用户授权和冻结政策执行。本文件不新增付费实验授权。
