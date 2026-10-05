# W07 / W08 补全操作与证据交接

日期：2026-09-26。本文区分软件通路、真实证据补齐和研究验收；不把待填材料当作通过证明。旧 run、旧协议、快照和账本不改写，不启动供应商请求，不消费真实留出行情。

最新W08补充见[文档20](20-w08-independent-confirmation-defaults.md)：已在新配置填写六项暂定验收值，旧区间独立性无法证明时暂缓执行、不冒充通过。原配置仍null；下文涉及“六项未配置”指旧配置的历史状态，新准备配置不再缺六项数值，但仍没有真实独立资格与accepted候选。

## 最新决定：历史费用暂不追补

用户已批准“历史费用暂不追补，未来改善采集，研究门槛不放松”。本批 `formal-five-20260924` 的决定绑定既有事件链，记录于 `configs/research/formal-five-20260924-accounting-policy.json`。历史费用/tokens仍为null，账务仍partial；不再把追补供应商账单作为继续因子研究的前置条件。没有费用清零、名额释放、自动重试或研究门槛豁免。

未来使用 `rdagent-single-dispatch-usage-v2`：在已安装的LiteLLM/RDAgent调用边界保存供应商响应ID、request ID（若返回）、请求模型/响应模型、实际输入/输出及缓存/推理tokens（若返回）。只保存白名单字段，不保存SDK配置、认证信息、响应头或SDK估算费用。`actual_cost`继续为null，直到有实际金额凭证；本地token估算继续与实际用量分开。

实际用量只采信LiteLLM `post_api_call`事件中的原始供应商响应，不采信SDK规格化时补出的0。缺失、非整数、负值和越界明细保持未知；没有原始响应事件时，即使SDK返回token计数也不冒充供应商实测值。原始事件整体、认证头和配置均不落盘。

worker在JSON解析前落盘 `provider_receipt.json`，因此解析失败不丢失已收到的usage；parent对序列化请求指纹、回执内容和hash进行核验，并把可验证usage带入uncertain账本。没有可信回执就保留未知，不能重发。供应商入口最多一次调用，SDK重试设为0、禁止截断自动续写；关闭RDAgent隐藏文本缓存，继续使用本项目有账本与hash验证的缓存。旧版本的“一次worker”等于“一次底层请求”假设并不充分，新的保护不追溯证明旧调用物理次数。

这改变了运行身份，后续正式研究需使用新protocol及匹配baseline，不能改旧协议或关闭hash检查。5次campaign、每trial 5 dispatch/2 repairs和8小时上限不变；已耗尽campaign不能复活。IC/ICIR方向规则、配对、消融、风险、交易成本和独立留出门槛均不变。

生成采用已批准费用政策的新派生报告时，在下方 `prepare-research-closure` 命令中使用：

```powershell
--accounting-policy configs/research/formal-five-20260924-accounting-policy.json
```

无需再传 `--billing-receipts`；只有将来确实获得回执时才做可选补充对账。输出 `accounting_disposition.status=deferred_by_user`、`historical_backfill_required=false`，原 `status=partial` 不变。此费用政策文件不是一份账单，也不证明费用已知。

## 1. 这次已补齐什么

- W07：新增 `prepare-research-closure`，从 campaign 事件链和各 run 的 billing 文件提取真实调用，生成逐笔对账表。可接收人工核对的供应商回执，在**新目录**生成补充视图；旧费用、执行状态、失败名额均保持原样。
- W08：历史只读审核继续支持 v1；实际 `evaluate-holdout`、worker 和 CLI 缓存复用现在必须通过 v2 访问审核。v2 绑定开始日、结束日、snapshot ID、manifest SHA256，核验审核人、带时区审核时间与来源 hash。单独设 `holdout_independent=true` 不再足以执行留出。
- 留出执行仍只使用已冻结模型、既有验收规则及一次性区间 claim。成本回执不能把不明执行改成“可重试”；访问审核不能升级研究 rejected 为 accepted。

## 2. 当前真实材料

目录：`artifacts/research_audits/plan18-w07-w08-closure-20260926/`。

| 文件 | 用法 |
| --- | --- |
| `billing_inventory.json` | 真实 call ID、request hash、run、stage、原 billing hash；10 条调用的费用与供应商 tokens 仍未知 |
| `billing_receipts.template.json` | 待人工补充的逐笔供应商回执映射；空白字段不产生对账通过 |
| `campaign_audit.json` | 5/5 次名额；R02/R04 终态失败但未提交 trial；保留名额、不重发 |
| `holdout_access_audit.template.json` | 2026-01-01 至 2026-09-04、冻结快照绑定的待审模板 |
| `independent_confirmation_preparation.json` | 本地访问记录搜索范围、旧协议状态、源 run 因子结果和后续步骤 |
| `manifest.json` | 源码 hash、输入证据 hash、输出文件 hash；零外部调用、未读留出值 |

R02 没有独立 `billing.json`，checkpoint 内记录 call_count=0，不能仅因文件缺失认定供应商实付为零。R04 有一条 hypothesis 调用但 usage/cost 未知。当前进程观察未发现 R02/R04 对应命令行，检查范围不等于所有历史进程的所有权证明。

本地 `artifacts/final_acceptance/usage` 未找到记录，只能说明这个入口没有本地留痕，**不能证明人工、Notebook、其他项目或历史回测未访问过同一时期**。

## 3. W07：可选费用补充（当前不要求追补）

1. 从实际供应商/代理平台导出本批调用对应的账单或 usage 明细，保留原文件。无需提供 API Key、密码或账号令牌。
2. 复制模板到新的审核文件，不覆盖生成目录。对每个 `call_id` 核对原 request hash；若旧 transport 未保存供应商 request ID，需要结合本地日志时间、模型、请求/响应指纹人工建立映射。无法唯一匹配就保持未知，不按估算 token 比例摊分账单。
3. 可独立补金额或 tokens，缺失项保留 `null`。填写原币种 `currency`，不能混加 USD/CNY。金额是非负十进制字符串；只有有凭证的免费请求才填 `"0"`。tokens 必须是供应商实际非负整数。
4. `billing_reference` 是逐调用唯一凭证标识；`mapping_basis` 说明映射依据；`reviewer` 和 `reviewed_at` 是实际审核人及带时区时间。`sources` 填原始导出文件的绝对路径、SHA256。多条调用可引用同一账单文件，但不能复用同一逐调用凭证标识。
5. 运行以下命令写入一个**未使用的新 run ID**，再检查 `billing_inventory.json` 的缺项数和按币种已知小计。全部已知小计为 0 且仍有未知项，绝不等于总费用为 0。

```powershell
& E:\tools\anaconda\envs\qlib_zhengshi\python.exe -m etf_ml.cli prepare-research-closure `
  --config configs/data/tushare_formal_first_loop.yaml `
  --source-run artifacts/runs/tushare-formal-first-loop-20260925-r05 `
  --campaign-id formal-five-20260924 `
  --billing-receipts D:\quant_project\ETF-Qlib\artifacts\review_inputs\billing-reviewed.json `
  --run-id plan18-w07-reconciled-v1
```

`Get-FileHash -Algorithm SHA256 -LiteralPath <绝对路径>` 可取得凭证 hash。该 hash 证明导出文件未变化，不自动证明账单内容或人工映射正确；审核人负责核对来源。

对账输出是补充视图，不改旧 campaign summary 的 partial；旧未提交 trial 仍明确保留。若供应商无法提供逐调用回执，可以把 W07 的**工程验收**与**历史费用未结**分开交接，不能伪造成本完整。`unlimited` 只表示金额不设硬上限；未知费用按既定政策记录，不自行添加停机金额线。该 campaign 已用满 5 次，不能借对账释放名额；任何后续研究使用新的有限 campaign 和匹配协议/基线。

## 4. W08：怎样补访问历史

待审区间为 **2026-01-01—2026-09-04**。需核查本项目、其他研究项目/Notebook、导出的收益图表、人工查看结果以及把这一时期用于挑选或调整因子的情况。

- 无法确定：`history_complete=null/false`，不声明独立。
- 曾用于选择或看过候选结果：相应 `prior_selection_use` / `prior_result_access=true`，旧区间不独立；不能通过更名因子/协议重新获得独立性。
- 有完整历史且确认未用于选择、未看过候选结果：具名填写审核时间、两项 false 和来源清单。只有事实支持时才填写 `history_complete=true`。

模板复制为审核文件后再填写；保留原模板。当前旧协议 `holdout_independent=false` 不改变，即使取得新审核材料，也不会追溯升级旧 run。新实验需在结果产生前冻结新的协议/快照身份、匹配基线与候选选择规则。

还发现一个必须预先解决的条件：当前配置的 `acceptance` 六项均为 `null`：最小净收益、最小超额收益、最大回撤、最大年化波动、最大执行成本/初始权益、最少有效日期。研究层已确认的 12% 回撤、2x 成本压力 -3% 不是完整的独立验收标准。新协议应沿用已批准风险边界，并在查看留出结果**之前**明确其余参数；不凭结果倒推阈值。

取得正式 accepted 研究候选后，按既有 `freeze-features → compare-models → freeze-model` 流程冻结完整包。冻结包必须保留定义/方向、特征处理器、模型权重、标签、成本、风险、调仓、协议和搜索谱系。再执行：

```powershell
& E:\tools\anaconda\envs\qlib_zhengshi\python.exe -m etf_ml.cli evaluate-holdout `
  --config <新的已冻结配置.yaml> `
  --frozen-model <已冻结模型包目录> `
  --holdout-access-audit <已完成人工审核的v2文件.json> `
  --run-id <新的独立确认run-id>
```

这是操作格式，**当前不要执行**：没有 accepted 因子，访问历史未知，独立验收六项未配置，旧协议未声明独立。代码变更也会改变源码/协议身份，不能关闭 hash 检查强行沿用旧 baseline。

## 5. 无可靠历史时的前瞻方案

不任意挑选“较新的历史区间”充当独立样本。候选实际通过正式研究并冻结后，再预注册前瞻开始日、最少成熟交易日/标签数、唯一验收时点、成本/风险阈值、数据完整性规则和停止条件。

前瞻期间只追加数据收据与版本固定的影子运行记录；不接券商、不下单。缺数据、身份漂移、账本不连续时停止并标记缺口；不为改善表现临时换因子、翻方向、调参数或提前窥视择优。期末一次性确认；失败后的修改是新候选，其前瞻样本重新开始。前瞻长度及未定验收阈值仍需结果产生前确定，本文没有擅自替用户填入。

## 6. 交接条件

W07 已按用户批准将工程验收与历史核销分开，历史账务保留partial且暂不追补；W08 仍需真实访问审核、新协议的独立验收参数以及实际 accepted 候选。费用政策不代替这些事实。真实研究结果保持 rejected，独立验证 not_run，投资准备 not_ready。

## 7. 本次测试结果

费用政策与未来采集改进后的最终回归：全量 `tests/unit` 加 `tests/integration/test_rdagent_usage_receipts.py`，**1035 passed、7 warnings、97.54秒，退出码0**。其中6项集成使用已安装RDAgent和LiteLLM消息/原始响应日志/规格化/JSON解析路径，仅替换网络completion边界；覆盖正常、缺usage、部分usage、截断、非法JSON和网络错误。没有真实供应商请求，不声称线上采集覆盖率已验证。其余integration本轮未重跑。

当前源码hash `b9c7f850a2f82d1989594058037661aaa503c296ec2f4b8d479d50e7aa11f8cc`；最终JUnit与政策/派生报告绑定于 `artifacts/research_audits/plan18-fee-policy-20260926-tests/evidence-manifest.json`。最新只读派生目录 `artifacts/research_audits/plan18-cost-deferral-20260926-r3` 保留10条费用及provider usage未知，明确历史追补已延期；35项保护源hash未变，0外部调用、未读真实holdout值。

上一阶段1008项单测及2项合成holdout集成的证据仍保留在 `artifacts/research_audits/plan18-w07-w08-tests-20260926/evidence-manifest.json`，只对应旧源码 `583a79f2222636ab528912aba61ee2e9400dd3e36b4b5adab859359e3ae28fc9`。本次按Ponytail最小改动原则复用既有账本和RDAgent调用链，没有新增供应商客户端或另建运行框架。
