# LLM 因子优化工程实现规格

日期：2026-09-21。状态：V1 基础实现完成。阶段投影、v2 提案语义、字段/特征目录、确定性摘要、同会话卡片、用量记录与结构化失败已落地；V2/V3 的检索、调度和诊断仍为拟议接口。

关联：[总方案](10-llm-factor-optimization.md)、[V1](11-llm-factor-optimization-v1.md)、[测试标准](14-llm-factor-test-standard.md)。优先扩展现有模块，仅在职责明确时新增小模块；不引入新的编排框架、向量数据库或多智能体服务。

## 1. 模块改动地图

| 模块 | 当前责任 | V1 拟改动 |
| --- | --- | --- |
| `research/context.py` | ResearchContext、FactorSpec | 增加版本化语义字段和 PromptContext 契约；保留旧版本读取 |
| `research/session.py` | build_context、基线身份 | 构造审核字段/特征目录，显式保留诊断假设 |
| `features/baseline.py` | 基线特征公式 | 提供对应说明映射及映射版本；实现和目录变更必须一起检查 |
| `adapters/rdagent/proposal.py` | 假设/提案及修复 | 传递完整理由，语义检查，使用阶段投影，去除占位值 |
| `adapters/rdagent/coder.py` | 代码生成和静态修复 | 使用最小编码上下文，消费结构化运行错误，共享修复额度 |
| `research/factor_worker.py`、`factor_engine.py` | 隔离执行及质量门 | 暴露可信 check_id/错误类型；支持选定简单公式的独立一致性校验 |
| `adapters/rdagent/feedback.py` | 开发反馈白名单 | 保留完整反馈，新增确定性摘要与证据引用；不得覆盖选择结果 |
| `research/controller.py` | 轮次与恢复 | 保存完整假设和研究卡片、摘要选择日志、版本/预算状态 |
| `research/llm.py`、`llm_worker.py` | 调用、缓存、传输 | 请求计量、生成上限适配、统一重试计数、完整调用身份 |
| `research/budget.py` | 金额账本 | 保持金额语义，关联用量证据；不把估计 token 换算成已确认账单 |
| `contracts.py`、`research/protocol.py` | 冻结配置与协议 | 新研究配置显式绑定 prompt/summary/schema/token/retry 版本 |
| 拟新增 `research/prompting.py` | 无 | 纯函数生成阶段投影、确定性裁剪、必需信息清单 |
| 拟新增 `research/research_memory.py` | 无 | 卡片保存和同会话确定性检索；不用 LLM 总结历史 |

后续诊断主要扩展 `research/diagnostics.py` 与已存在的预测/回测工件读取；不得另写一套收益或费用计算口径。

## 2. 数据契约

### 2.1 HypothesisRecord 与 FactorProposal v2

| 字段 | 规则 |
| --- | --- |
| `schema_version` | 显式版本，禁止依赖静默默认升级旧记录 |
| `hypothesis`、`reason` | 均为非空解释；完整流经 proposal 和 trial |
| `mechanism` | 说明哪个可见现象可能影响目标；不声称已验证 |
| `direction`、`direction_reason` | positive/negative/unknown；unknown 必须有理由，不按 IC 强制反号 |
| `expected_difference` | 说明与已有特征的具体差异；拒绝已知占位值 |
| `compared_features` | 已有特征 ID 列表；新类别可以空，但须解释为何没有可比特征 |
| `failure_conditions` | 至少一条可观察的失效条件，不泛写“市场变化” |
| `research_group` | 审核过的机制类别；新类别需明确注册，不用 unclassified 自动混入消融 |
| 现有公式/字段/时间/窗口/缺失字段 | 保持；新增定义不得掩盖现有时点约束 |
| `context_hash`、`parent_trial_id` | 完整本地上下文身份和可选父试验身份，由程序填写 |

结构检查可执行，经济真实性不可仅靠规则证明。固定盲评量表评估机制是否可检验、差异是否具体、信息时点是否合理、结论是否越界。

`lookback` v2 明确定义为连续观测下包含当日的最小输入跨度；`minimum_observations` 为无缺失条件下首次可得值所需行数。停牌、缺失与日历窗口另行声明。若改变旧字段含义，必须新 schema，并用显式迁移解释旧记录；不能重写原 spec 哈希。

### 2.2 FieldDescriptor / FeatureDescriptor

字段目录包含 name、meaning、unit、adjustment、available_at、missing_policy、allowed_usage、evidence_id。可用时点来自真实数据契约；“after_daily_ingestion”不足以证明历史 PIT。未知语义字段默认不进入新提案允许输入。

特征目录包含 feature_id、formula、lookback、required_fields、group、direction_note、missing_policy、definition_hash。压缩版保留可理解公式及窗口，不能只提供哈希。

基础目录固定；新入库因子从已校验 spec 生成。禁止从任意 docstring 或模型自由文本自动生成“已验证”说明。

### 2.3 PromptContext

```text
schema_version, stage, prompt_version, projection_policy_version
snapshot_id, protocol_id, baseline_id, full_context_hash
task_contract, qualification, allowed_fields, relevant_features
current_hypothesis / current_proposal
selected_memory_cards, feedback_summary
output_schema, runtime_constraints
```

本地 envelope 另存 payload_hash、必需信息清单及 included/omitted 的理由、tokenizer/version、estimated_input_tokens、裁剪过程。不要为了审计把完整 envelope 再发送给 LLM。

`full_context_hash` 校验完整研究身份，`payload_hash` 校验实际发送内容；二者不能混用。相同输入与版本应生成字节稳定的 PromptContext。

### 2.4 FeedbackSummary

```text
summary_version, stage=factor_selection
factor_version_id, baseline_id, protocol_id, snapshot_id
status, reasons, dominant_issue
quality: coverage / worst_coverage / time_check / unknown_fields
fold_summary: median_delta / min_delta / max_delta / positive_seeds
risk_attribution: baseline_breach / candidate_breach / incremental_change
redundancy, uncertainty, attempted_trials, uncertainty_note, unresolved_questions
next_test: action_code / rationale / evidence_ids
source_evidence_hash
```

约束：

- summary 是旁路解释，compare 的完整精度结果是唯一入选依据。
- dominant_issue 由固定优先级选取，但 reasons 保留全部拒绝码；不能删掉不利项换取短文本。
- seed 汇总仍以折为时间单位。基线和候选都有超限时显示 inherited 风险，同时显示候选改善或恶化。
- 舍入数值附完整决策状态；临界值增加 near_threshold。NaN、缺失和无有效样本统一明确 unknown，不填 0。
- 下一步是待验证建议。没有预测/成交证据时不输出确定性归因。
- 保留已尝试次数和“开发期统计未消除重复选择偏差”的限制；bootstrap_fraction_positive 不能改写为因子有效概率。

### 2.5 ResearchCard

保存完整假设、规范化公式、字段/窗口、定义和输出身份、基线/协议/快照、选择结论、主失败原因、技术修复历史及摘要证据。`factor_id` 不足以确定语义身份。

首版 `select_cards` 使用最近记录和结构相关性，固定排序及 tie-break；先去重，再在预算内选择。失败案例必须参与检索。每张卡片附是否与当前数据/协议可比；跨版本的旧数值不能直接作为当前收益证据。

## 3. 请求计量、缓存和重试

### 3.1 UsageRecord

| 字段组 | 内容 |
| --- | --- |
| 身份 | logical_call_id、transport_attempt_id、trial_id、stage、模型/提供商/配置指纹 |
| 内容 | prompt_hash、response_hash、各契约/提示词版本 |
| 本地计数 | input_chars、output_chars、estimated_input/output_tokens、tokenizer 与版本 |
| 服务商用量 | input/output/cached/reasoning_tokens、usage_source、provider_request_id；不支持则 null |
| 结果 | status、finish_reason、latency、retry_kind、cache_kind、计量完整性 |
| 金额 | actual_cost、currency、cost_source；未知为 null，保留现有账本状态 |

`usage_source` 至少区分 provider / tokenizer_estimate / unknown；数字 0 只用于确实观测到零。不同提供商的 cached/reasoning token 可能是 input/output 的子集，归一化时保留原始字段和计算说明，不能重复相加。

已有 `llm_worker` 只返回文本，优先验证本机 RDAgent 0.8.0 是否能提供受支持的 usage 接口/回调；如需新适配器，必须经传输兼容测试。不要把不完整日志解析成服务商实际账单。

### 3.2 缓存身份

应用请求缓存键应绑定实际 prompt 字节、system、阶段、schema/summary/projection 版本、模型和提供商身份、生成参数、输入身份及预算策略。服务商密钥不写入明文身份；账号隔离如需采用非敏感命名空间。

完整内容相同才可复用响应；同公式但不同基线、输出格式、模型版本或温度不能复用旧 LLM 结果。命中验证 manifest、response 和账本证据，记录本次没有新物理请求，不能重复加总原调用费用。

因子输出、基线回测缓存继续沿用现有完整性规则；第一版不为提升命中率删掉上下文/代码依赖。

### 3.3 统一重试

区分格式/语义修复、执行修复、SDK/网络重试和中断恢复。拟议上限：每逻辑调用最多 3 次物理尝试（包含底层重试），每阶段 1 次模型修复，每候选共 2 次模型修复。

当前 worker 设置 max_retry=3，外层 transport 也有最多 3 次尝试。必须先核实库实际尝试语义，再将重试收敛到可审计的一层；不能直接声称当前最多 3 次网络请求。

发送后连接中断可能已计费。可核实、可幂等恢复时按证据恢复；无法确认时记录 uncertain 并保留预算记录，不盲目重发。恢复已有成功 response 不消耗新的逻辑调用额度。所有已发生尝试，即使因异常无法得到 token 用量，也在 coverage 中计为未知。

## 4. 错误与修复边界

拟议 `ValidationFailure`：stage、category、check_id、expected、observed、sanitized_message、recoverable、source_hash、evidence_id。完整日志本地保存；提示词只接收 allowlist 投影，按字节/文本大小限制，去除主机路径、凭据及数据行。

| 类别 | 处理 |
| --- | --- |
| json_format / schema / placeholder | 同定义有限修复；原响应仍保存 |
| syntax / index_shape / numeric_type | 可修复实现，必须重新跑全部相关检查 |
| warmup / formula_mismatch | 可修复代码以匹配已冻结定义；若需改定义，提出新候选 |
| causal_violation / forbidden_access | 拒绝该实现，不能压缩成普通语法错误绕过 |
| timeout / memory_limit | 保存技术失败；不自动增加资源或不停重试 |
| data_contract_failure | 数据链路失败，不要求 LLM 编造缺失字段 |
| transport_uncertain | 按调用恢复政策处理，不视为因子失败后再无界重发 |
| no_increment / risk_rejected | 正常研究结论，不能触发“修复直到通过” |

运行分类由可信 worker/控制器生成，模型不能自行声明通过。AST 加因果数值检查继续保留。简单公式 oracle 使用人工可核算数据和独立实现，不能用候选本身给自己生成期望值。

## 5. 状态、工件与恢复

拟议状态流：context_prepared → hypothesis_validated → proposal_validated → code_validated → factor_materialized → evaluated → summarized → committed。每阶段可进入明确 failed/rejected/inconclusive；沿用现有外层 trial 状态映射，不随意新增无法被 CLI 识别的终态。

每 trial 拟增加：

```text
hypothesis.json
proposal.json
prompt_manifest.json
feedback_summary.json
research_card.json
usage_summary.json
repair_history.json
```

实际 request/response 和执行日志复用现有 llm/calls、transport、workspace 目录，避免复制大内容。`trial.json` 绑定这些工件的哈希；只有完整落盘并验证后才提交 checkpoint。并发计量使用已有锁/原子写能力。

恢复时校验源码、协议、提示词投影、schema、tokenizer/预算版本和已提交工件；任何身份变化拒绝继续旧 trial。恢复必须得到相同卡片选择、提示词和额度剩余值。未提交半成品不能成为缓存或研究结论。

## 6. 配置与兼容

以下仅为字段设计，不可直接复制为当前可运行 YAML：

```yaml
research:
  prompt_policy:
    version: etf-factor-v2
    schema_version: factor-proposal-v2
    summary_version: feedback-summary-v2
    projection_version: stage-context-v1
    recent_cards: 2
    relevant_cards: 3
    memory_token_cap: 1600
    max_repairs_per_stage: 1
    max_repairs_per_trial: 2
    max_transport_attempts_per_call: 3
    # 各阶段 input/output 预算按第一版文档显式配置。
```

ResearchPolicy、配置解析、ComparisonProtocol 和会话保存必须同步支持；Pydantic 严格 schema 对未知字段继续拒绝。旧 JSON 加载若增加默认字段会改变哈希，需要版本化读取/迁移测试，不能只给字段加默认值便认为兼容。

旧协议与旧运行继续只读。新版开新 run 和新冻结协议；回滚是使用原代码与原配置创建/恢复匹配身份的会话，不能把新版工件塞回旧 checkpoint。

## 7. 实施拆分

1. 计量与版本：UsageRecord、对照夹具、请求/重试身份及旧版本读取。
2. 语义与上下文：目录、完整假设、提案校验、阶段投影和预算。
3. 摘要与卡片：确定性反馈、有限历史和精确重复检查。
4. 修复与公式一致性：可信错误分类、有限修复、人工 oracle。
5. 集成与对照：无付费多轮回放、真实容器/模型，再做有限真实 LLM 验收。

每一步均有独立测试证据和回滚边界。优化期间不改变已运行 v20 的任何源产物。
