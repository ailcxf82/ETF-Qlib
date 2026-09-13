# RDAgent + Qlib 机器学习 ETF 投资框架：工程文档

版本：设计草案 v0.1 ｜日期：2026-09-13 ｜状态：待实现

本套文档定义从 ETF 数据治理、因子自动研发、机器学习训练到组合回测和每日信号的完整框架。必选主模型为 LightGBM，即 Qlib 的 `qlib.contrib.model.gbdt.LGBModel`；用户提到的“LBGLight”暂按此理解。RDAgent 的职责是提出和实现能改善机器学习系统的特征，Qlib 承担数据集、模型及回测基础设施。

## 阅读顺序

| 文档 | 解决的问题 | 主要读者 |
| --- | --- | --- |
| [01 系统设计](01-system-design.md) | 目标、模块职责、接口、运行环境和完整流程 | 项目负责人、开发者 |
| [02 数据与机器学习规范](02-data-and-ml-contracts.md) | ETF 数据、时点、标签、LightGBM 与其他模型、验证切分 | 数据与模型开发者 |
| [03 RDAgent 因子研发规范](03-factor-research.md) | 因子任务约束、实验对照、评分、入库和反馈 | 研发适配层开发者 |
| [04 开发计划与验收](04-delivery-plan.md) | 依赖顺序、文件交付、测试、运行维护、风险和决策 | 全体参与者 |

## 核心设计决定

1. 第一版默认针对境内股票 ETF，日频计算、长仓轮动、允许现金；其他 ETF 类别通过元数据与交易规则接口扩展。该范围是可调整的设计假设。
2. 采用 5 个交易日预测跨度，每 5 个交易日调仓；1 日跨度作为诊断对照。先验证固定特征 + Ridge/LightGBM，再接 RDAgent。
3. 先冻结模型、标签、组合和成本规则，评价新增因子的条件增量。模型比较与调参进入独立阶段。
4. 训练、早停验证、研发选择、最终留出分离。Agent 接触不到最终留出数据、统计和回测产物。
5. 将可运行、可信评估和可投资分别验收。即使研究没有找到更优因子，完整可复现的拒绝结论也属于工程成功。

## 本机事实与边界

- 已核实的原始 ETF 数据路径：`D:\qlib_data\etf_qlib_data`。用户原始输入与路径解释见 [项目约定](../PROJECT_CONTEXT.md)。
- 原始数据只读；拟在项目 `artifacts/data/<snapshot_id>/` 中生成标准化视图，不覆盖原始数据。
- 本机环境为 `qlib_zhengshi`；已核对版本：RDAgent 0.8.0、pyqlib 0.9.7、LightGBM 4.6.0、pandas 2.3.3、NumPy 1.26.4。
- 当前项目已有 `.env.example`、环境检查脚本和历史运行日志；尚无本套框架的适配层与端到端验收结果。
- 既有数据抽查发现日历含周日、部分复权因子缺失、涨跌幅字段语义和成交量单位待核实。这些是待修复/核查项，不代表已完成全量数据审计。
- 本次交付仅为工程设计文档，没有启动付费因子研发、训练或实盘下单。文档里的模块、命令及配置片段均为待开发规格，除明确标为“已有”的内容外不可直接视为现有功能。

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
