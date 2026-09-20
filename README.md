# ETF-Qlib

基于 RDAgent + Qlib 的机器学习 ETF 投资研究框架。已实现工程基座、数据审计/快照、特征与时序数据集、Ridge/LightGBM/XGBoost、等权池/手工动量和 Qlib ETF 基线回测、隔离因子引擎、固定协议配对实验、开发期紧凑结构化反馈、每日独立账本、因子生命周期注册库，以及 RDAgent ETF 适配与可恢复多轮回放。已入选因子跨轮累积、候选组消融、最终特征全组复核与冻结、冻结特征多模型比较、显式完整模型版本冻结及版本注册已接入；独立留出推断/阈值验收、项目级区间禁用及技术重试已接入；每日影子信号、监控及完整版本恢复已有工程实现；真实 LLM、正式独立投资验收与真实交易日影子验收仍待完成；真实数据 G0 尚未通过。

- [首轮闭环缺口与优先级](docs/08-first-loop-readiness.md)：P0阻塞项、快速检查及真实运行顺序。
- [实现进度与验收证据](docs/05-implementation-status.md)：当前可运行入口、测试与真实数据缺口。
- [真实数据源语义证据](docs/07-data-source-evidence.md)：全量 CSV/bin 核对、字段映射与剩余 G0 条件。
- [工程文档入口](docs/README.md)：系统架构、数据与模型、因子研发、实施及验收。
- [项目约定](PROJECT_CONTEXT.md)：ETF 数据路径与已确认上下文。
- [环境检查脚本](scripts/check_etf_environment.py)：已有的依赖、数据和训练 smoke 检查。

首版必选模型为 Qlib `LGBModel`（LightGBM），配套 Ridge 基线和可扩展模型接口。RDAgent 的候选因子按固定机器学习及组合协议检验增量价值。

原始 ETF 数据：`D:\qlib_data\etf_qlib_data`。原始目录只读；标准化快照和实验产物拟写入项目独立目录。
