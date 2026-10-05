# RDAgent + Qlib 机器学习 ETF 投资框架：工程文档

- [轻量结果复用与共享基线](25-compact-reuse.md)：独立结果包、迁移查询与清理预览。

版本：设计草案 v0.1 ｜日期：2026-09-13 ｜状态：实施中；当前功能与验收证据见 [实现进度](05-implementation-status.md)

本套文档定义从 ETF 数据治理、因子自动研发、机器学习训练到组合回测和每日信号的完整框架。必选主模型为 LightGBM，即 Qlib 的 `qlib.contrib.model.gbdt.LGBModel`；用户提到的“LBGLight”暂按此理解。RDAgent 的职责是提出和实现能改善机器学习系统的特征，Qlib 承担数据集、模型及回测基础设施。

## 阅读顺序

最新专项：[23 下一轮因子发现与 ETF 研究排名](23-next-round-factor-discovery-and-daily-ranking.md)（2026-09-29，拆分风险触发与验收门槛，记录下一轮筛选、验证及当前阻塞）。

| 文档 | 解决的问题 | 主要读者 |
| --- | --- | --- |
| [01 系统设计](01-system-design.md) | 目标、模块职责、接口、运行环境和完整流程 | 项目负责人、开发者 |
| [02 数据与机器学习规范](02-data-and-ml-contracts.md) | ETF 数据、时点、标签、LightGBM 与其他模型、验证切分 | 数据与模型开发者 |
| [03 RDAgent 因子研发规范](03-factor-research.md) | 因子任务约束、实验对照、评分、入库和反馈 | 研发适配层开发者 |
| [04 开发计划与验收](04-delivery-plan.md) | 依赖顺序、文件交付、测试、运行维护、风险和决策 | 全体参与者 |
| [10 LLM 因子质量与 Token 优化总方案](10-llm-factor-optimization.md) | 当前证据、完整优化清单、阶段边界 | 项目负责人、研究者 |
| [11 第一版优化建议](11-llm-factor-optimization-v1.md) | 首批改动、Token 预算、发布条件 | 研发适配层开发者 |
| [12 后续优化建议](12-llm-factor-optimization-next.md) | 深层诊断、多轮检索、调用合并与模型路由 | 项目负责人、研究者 |
| [13 优化工程实现规格](13-llm-factor-engineering.md) | 模块、数据契约、计量、缓存、修复和恢复 | 开发者 |
| [14 优化测试与验收标准](14-llm-factor-test-standard.md) | 测试矩阵、质量门槛、Token 对照及验收证据 | 开发者、验收者 |
| [15 因子能力与去重优化实施方案](15-rdagent-factor-capability-implementation.md) | 项目记忆修复、因子/实验身份、分级去重、搜索配额、42 项验证场景及测试命令 | 开发者、研究者、验收者 |
| [16 正式因子研究闭环优化工程规格](16-formal-factor-research-optimization.md) | r1–r25 证据、验收可达性、反馈送达、证据复用、风险归因、跨运行审计、分批实施与 20 项验收场景 | 开发者、研究者、验收者 |
| [17 因子库接入与筛选验证方案](17-factor-library-integration-and-screening.md) | Alpha101/Alpha360 等来源的准入分层、数据映射、筛选流程、当前候选/淘汰/绕开结果与验收计划 | 开发者、研究者、验收者 |
| [18 整体因子研究优化规划](18-integrated-factor-research-optimization-plan.md) | 可信状态、基线风险、成熟因子批筛、期限/ETF暴露适配、反馈/费用审计、独立确认；8个工作包与22项验收 | 项目负责人、开发者、研究者、验收者 |
| [19 W07/W08 补全操作与证据交接](19-w07-w08-closure-guide.md) | 逐调用费用回执、只读补充对账、v2留出访问审核、独立验收缺项及前瞻边界 | 项目负责人、费用审核人、研究者 |
| [20 W08默认参数与暂缓方案](20-w08-independent-confirmation-defaults.md) | 六项默认验收线、来源与判断边界、缺证据暂缓、未来预注册规则与测试 | 项目负责人、研究者、验收者 |
| [21 研究限定方案B操作手册](21-research-only-plan-b-runbook.md) | 冻结快照替代重新采集、真实campaign预检、基线runtime身份、W08暂缓而不冒充通过 | 开发者、研究者、运行负责人 |
| [23 下一轮因子发现与 ETF 研究排名](23-next-round-factor-discovery-and-daily-ranking.md) | 风险触发/验收拆分、有限因子筛选、五次正式尝试与每日 ETF 排名路径 | 项目负责人、开发者、研究者、验收者 |

2026-09-25 更新：文档18是下一阶段整体规划，已核对当前源码与R05报告；文档17同步修正Alpha360列数、campaign计数和G0字段解释。日级风险诊断与campaign台账已有实现，进一步验收和缺口见18；下方2026-09-24说明保留为历史进度，本次未实施新增研究功能或启动新campaign。

2026-09-24 更新：文档 16 根据 25 轮运行后的诊断补充工程优化规格。E01–E03 与模型/Bootstrap 种子标注已实施并完成离线自测；日级风险归因、跨运行 campaign 台账和有界搜索仍待实施。未恢复研究；正式研究 accepted、独立确认与投资准备分开验收。

2026-09-22 新增：文档 15 是下一阶段待实施规格，包含模块改动、数据契约、工作包、验证方法和回滚边界；本次仅交付文档，不代表新增功能或测试已完成。10–14 中部分基础能力已有实现，具体范围以各文件执行记录和当前代码为准。

2026-09-21 更新：v20 已完成真实 LLM 诊断闭环，候选被正常拒绝；正式 G0 与独立投资验收未通过。最新证据见 [09](09-first-real-loop.md) 和 [10](10-llm-factor-optimization.md)。10–14 是待实施的优化设计，本文下方 2026-09-13 的环境和数据进度为历史背景，不能替代最新运行报告。

## 核心设计决定

1. 用户已确认第一版针对 A 股场内现有正常运营的 ETF，按跟踪境内 A 股的股票 ETF 实现，日频计算、长仓轮动、允许现金；历史池按当时已知状态重建。主基准为沪深300指数，初始资金 50 万元，单边滑点 0.03%；确认细则见 [项目约定](../PROJECT_CONTEXT.md)。
2. 采用 5 个交易日预测跨度；用户已将调仓频率改为每月月中和月末，预测跨度与实际持有期不再视为相同。月中暂按15日或之前最近交易日、月末按最后交易日执行，使用前一交易日收盘后信号。1 日跨度作为诊断对照。佣金按最新确认0.03%；K=前5%、流动性=前20日均成交额参与上限20%、最大回撤12%已冻结；LLM费用不设上限；暂按比例收费、不另加最低费用。先验证固定特征 + Ridge/LightGBM，再接 RDAgent。
3. 先冻结模型、标签、组合和成本规则，评价新增因子的条件增量。模型比较与调参进入独立阶段。
4. 训练、早停验证、研发选择、最终留出分离。Agent 接触不到最终留出数据、统计和回测产物。
5. 将可运行、可信评估和可投资分别验收。即使研究没有找到更优因子，完整可复现的拒绝结论也属于工程成功。

## 本机事实与边界

- 已核实的原始 ETF 数据路径：`D:\qlib_data\etf_qlib_data`。用户原始输入与路径解释见 [项目约定](../PROJECT_CONTEXT.md)。
- 原始数据只读；拟在项目 `artifacts/data/<snapshot_id>/` 中生成标准化视图，不覆盖原始数据。
- 本机环境为 `qlib_zhengshi`；已核对版本：RDAgent 0.8.0、pyqlib 0.9.7、LightGBM 4.6.0、pandas 2.3.3、NumPy 1.26.4。
- 当前实现见 [实现进度与验收证据](05-implementation-status.md) 和 [真实闭环记录](09-first-real-loop.md)。已有基线合成测试、固定镜像因子测试、RDAgent ETF 扩展、多轮确定性回放及 v20 真实 LLM 诊断闭环；诊断闭环不等于真实数据 G0 或 G1–G4 正式门禁通过。
- 真实数据已完成全量只读审计、1,769只ETF的CSV/bin语义核对和沪深公告日历独立核对，见 [数据源证据](07-data-source-evidence.md)。原日历340个休市日期仍须在新快照重编码，复权/企业行为、历史PIT状态和沪深300行情仍须补齐；尚未通过G0。
- 本套文档定义完整工程规格；当前已实现和测试的功能以 [实现进度](05-implementation-status.md) 及 [近期真实闭环](09-first-real-loop.md) 为准。已开展付费 LLM 诊断研究；正式数据与投资验收状态按具体报告分别判断。未有实现及验收证据的模块、命令和配置片段仍为开发规格。

## 需求追踪

| 编号 | 需求 | 设计位置 | 验收 |
| --- | --- | --- | --- |
| R01 | 使用正确的 ETF 数据源及历史池 | 02 §1–2 | G0 数据准入 |
| R02 | Qlib 必须包含 LightGBM | 02 §5 | G1 固定模型基线 |
| R03 | 支持其他机器学习模型 | 02 §5–6 | G1 Ridge；G3 扩展模型 |
| R04 | RDAgent 因子满足机器学习框架需求 | 03 §1–5 | G2 同条件增量实验 |
| R05 | ETF 组合、成本、交易约束回测 | 01 §5；02 §7 | G1 交易账本测试 |
| R06 | 无未来数据泄漏、实验可追溯 | 02 §3–4；03 §6 | G0–G4 各阶段检查 |
| R07 | 可重复的日常预测与版本恢复 | 01 §6；04 §5 | G4 影子运行 |

## 参考依据

技术接口优先按本机安装源码核验；在线文档可能对应比本机更高的版本，不据此假定本机具备新功能。

- [Qlib 模型接口与 LightGBM](https://qlib.readthedocs.io/en/latest/component/model.html)
- [Qlib 数据接口与 Dataset](https://qlib.readthedocs.io/en/latest/component/data.html)
- [Qlib 实验记录](https://qlib.readthedocs.io/en/latest/component/recorder.html)
- [Qlib Exchange 源码](https://github.com/microsoft/qlib/blob/main/qlib/backtest/exchange.py)
- [RDAgent 配置与安装](https://github.com/microsoft/RD-Agent/blob/main/docs/installation_and_configuration.rst)
- [RDAgent 因子 Runner](https://github.com/microsoft/RD-Agent/blob/main/rdagent/scenarios/qlib/developer/factor_runner.py)
- [LightGBM 参数](https://lightgbm.readthedocs.io/en/stable/Parameters.html)
- [LightGBM 排序模型接口](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.LGBMRanker.html)
