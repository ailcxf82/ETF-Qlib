# 方案B：缺少独立验证材料时继续受控研究

日期：2026-09-26。范围：修复实际预检缺口，利用已验证冻结快照继续研究准备；不将缺数据、未验证或经济拒绝变成通过。

## 1. 已落到执行入口的行为

`first-loop-readiness` 增加 `--snapshot`、`--baseline-root`、`--check-runtime`。默认不写campaign、不占名额、不训练、不请求供应商；输出结构化的 `data_route`、`baseline`、`runtime`、`campaign_observation`、`deferred_stages` 和 `not_checked`。

| 条件 | 实际处理 | 能否绕过 |
| --- | --- | --- |
| 原始采集目录或上游服务暂不可用，但已有匹配的冻结快照 | 显式传`--snapshot`，核验所有登记文件hash、配置/股票池身份、结构/PIT资格及研究索引；成功后不要求重新读取原始目录 | 不重新下载，不改冻结工件；损坏快照不回退成另一种数据 |
| 没有冻结快照且必要原始行情/复权/日历/PIT/基准缺失 | 返回真实blocker，停止正式研究；可做现有合成测试检查软件，但不得把合成结果称为真实研究 | 不能补1、补0或套行业均值 |
| 独立留出访问历史未知、没有accepted冻结候选、缺前瞻样本 | `independent_confirmation=not_run`，不阻塞研究准备；已有独立执行入口继续要求v2审核与冻结模型 | 不能改`holdout_independent`为true来通过 |
| 来源完整性仍unknown，但冻结快照结构/PIT等研究检查通过 | 保持研究范围，显示完整unknown原因；不宣称G0/投资就绪 | 不把unknown升级为passed；任何已确认结构失败仍阻止 |
| 旧campaign用满或过期 | 预检直接blocked；旧名额不释放 | 不重启、不扩容旧campaign |
| 旧campaign无时限或时限不匹配当前冻结策略 | 预检明确`campaign_duration_mismatch` | 不补写旧账本 |
| 旧基线配置、源码或环境不同，或没有worker身份回执 | 明确拒绝复用，要求新基线 | 不能改旧request/报告hash以复用 |
| Docker Linux或固定镜像不可用 | 使用`--check-runtime`提前返回blocked | 不在预检中自动拉镜像、改环境或请求LLM |

快照完整性检查会读取包括holdout在内的文件字节用于hash，但不会解码留出价格、预测或收益；只读取研究panel的索引检查隔离边界。基线只在开发折进行训练/回测。

## 2. 已确认的实际缺口

修复前对 `formal-five-20260924` 的预检返回 `blockers=[]`，但该campaign已经5/5用尽。本次改为只读审计真实事件链，不再只校验ID与上限格式；同时发现旧campaign没有当前要求的8小时时限，不能在旧ID下补写。

复核旧基线复用入口时发现它比较配置却未绑定worker实际源码/环境。本次在baseline请求及worker结果中加入双向 `execution_identity`，worker执行前后校验，parent核对结果；复用入口必须与当前runtime一致。没有这一证据的旧基线保留，但不能作为本次匹配基线。

原始数据、2026-09-21基线、-0.03阈值基线、旧campaign和旧研究工件全部保留。W08六项默认值见[文档20](20-w08-independent-confirmation-defaults.md)，本次不调整任何研究经济阈值。

## 3. 当前只读检查命令

以下新campaign ID只是预检建议，不代表已创建或获准进行付费调用。

```powershell
& E:\tools\anaconda\envs\qlib_zhengshi\python.exe -m etf_ml.cli first-loop-readiness `
  --config configs/data/tushare_formal_w08_preparation.yaml `
  --snapshot artifacts/formal_tushare/data/8b384b5d9f8fa10d3d7b6aebbf11fd9463dc76cc9fe203d38dfc6f5ad4322831 `
  --baseline-root artifacts/runs/tushare-formal-w08-plan-b-baseline-20260926 `
  --campaign-id formal-five-w08-plan-b-20260926 `
  --campaign-max-trials 5 `
  --check-runtime
```

上面命令已绑定本次完成的新基线，终审为 `baseline.status=reusable`、`blockers=[]`。省略`--baseline-root`仍会显示`build_new`，不是已有基线通过；指定不兼容旧基线会blocked，不自动换到新实验。预检始终不认证供应商联网、扣费或因子有效性；`needs_full_validation`不是“已通过独立验证”。

当前冻结快照已通过真实全文件hash与结构检查，研究索引含1480个instrument；这不同于原始provider的1769个instrument口径。来源完整性仍有 `adjustment_source_completeness:unknown` 和 `events_source_completeness:unknown`，因此保持研究限定，不能宣称数据完全合格。

## 4. 新基线及后续运行边界

本次本地基线：`artifacts/runs/tushare-formal-w08-plan-b-baseline-20260926`，2026-09-26 20:48北京时间完成，主进程退出码0。该基线不调用LLM；最终审计见下节及[实施证据最新补记](18-implementation-evidence.md)。运行期间也实际验证过复用入口拒绝未完成基线，不能据目录存在认定完成。

新基线完成并核验后，下一次真实RDAgent仍需显式新运行指令；不得由预检自动分派。采用新有限campaign、一个run一个候选、保留失败证据、最多5次尝试；`unlimited`仅是金额处理政策，不解除次数/时限/修复上限。供应商配置存在不证明账号可用，实际usage缺失仍unknown，历史费用继续按用户批准暂不追补。

执行独立确认仍需实际accepted候选、新冻结包、可信独立数据和v2访问审核；前瞻期起止日期必须在观察前登记。方案B的成功条件是**研究通路可以在合格输入上运行，缺失的独立阶段如实暂缓**，不是保证找到盈利因子。

## 5. 验证范围

- 单测：旧campaign耗尽/过期/时限不匹配/事件链篡改；Docker不可用；旧基线缺少或改变runtime身份；W08保持not_run。
- 合成真实集成：由真实snapshot构建器生成文件，暂时移开测试用原始目录后仍可预检冻结快照；禁止读取holdout；错误hash、配置差异、研究索引混入留出日期全部拒绝；实际baseline子进程运行、worker身份回执与基线复用路径。
- 实际只读检查：固定镜像可用、冻结快照hash有效、旧campaign真实拒绝；新campaign预检不创建目录。
- 实际本地计算：新配置的开发折基线，不执行新LLM因子试验，不读独立留出收益。

Ponytail取舍：复用已有snapshot、campaign、Docker和baseline实现，未建立替代数据生成器、第二套验收器或跳过安全门槛的开关。

## 6. 最终工程验收（2026-09-26）

结论：本次研究准备及方案B工程补齐完成，已有匹配真实基线可供下一次有限campaign使用；W08真实独立确认和整个文档18研究规划不因此变成完成。

- 实际完成A—E五折、10个模型运行、10个辅助策略运行；基础和2倍成本共40组结果。643项run清单hash、10个模型包hash及源码/环境身份全部匹配，40组账本均对平。
- 40条开发回测收益序列核验通过：日期唯一、与报告索引hash一致、全部早于2026-01-01留出边界。未执行独立留出评估。全文件hash检查不等于取得独立数据访问许可。
- 终态复用预检：冻结快照有效、Docker固定镜像通过、基线可复用、无P0阻塞；W08六项acceptance均已填写。旧campaign保持5/5耗尽且无当前8小时时限，35项历史保护源未变；新campaign未创建，真实供应商调用为0。
- 自动化证据：1049项单测/准备集成、1项真实RDAgent/Docker离线回放均通过；没有把它们冒充付费供应商连通测试，也未声称重跑所有integration。
- 证据索引：`artifacts/research_audits/plan-b-preparation-20260926-tests/evidence-manifest.json`；最终只读输出为同目录`final-readiness.json`。早先`verification-progress.json`保留原时间戳，不覆盖。

预检输出`not_checked`表示该只读命令不重新执行训练/外部认证；本次基线是否实际完成及环境身份，另由终态报告、worker回执与本节完整性审计证明。来源完整性的两个unknown、供应商实际连通/收费，以及独立确认仍未被认证。

### 折级IC / ICIR诊断

下表来自本次**基线模型预测**的开发折报告，ICIR沿用非年化的日IC均值/标准差口径；不等同于新增因子的方向化验证，不设置跨折投票或汇总通过规则。

| 折 | 有效IC日期 | Ridge IC | Ridge ICIR | LightGBM IC | LightGBM ICIR |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | 124 | 0.014177 | 0.059847 | 0.044302 | 0.225108 |
| B | 117 | -0.072209 | -0.225697 | 0.068126 | 0.312347 |
| C | 125 | 0.012473 | 0.044859 | 0.014739 | 0.062702 |
| D | 117 | 0.065716 | 0.254022 | 0.010799 | 0.049176 |
| E | 120 | 0.054523 | 0.194466 | 0.055058 | 0.288835 |

Ridge的B折保留负值，不在看过开发评估结果后翻向或取绝对值。用户允许的反向因子仍须在训练期确定方向并冻结；本次只是复核报告可用，不产生accepted新因子，更不产生买卖指令。
