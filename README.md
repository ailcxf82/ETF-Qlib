# ETF-Qlib

基于 RDAgent + Qlib 的机器学习 ETF 投资研究框架。当前处于工程设计阶段，已有环境检查脚本，核心适配层与完整回测流程待开发。

- [工程文档入口](docs/README.md)：系统架构、数据与模型、因子研发、实施及验收。
- [项目约定](PROJECT_CONTEXT.md)：ETF 数据路径与已确认上下文。
- [环境检查脚本](scripts/check_etf_environment.py)：已有的依赖、数据和训练 smoke 检查。

首版必选模型为 Qlib `LGBModel`（LightGBM），配套 Ridge 基线和可扩展模型接口。RDAgent 的候选因子按固定机器学习及组合协议检验增量价值。

原始 ETF 数据：`D:\qlib_data\etf_qlib_data`。原始目录只读；标准化快照和实验产物拟写入项目独立目录。
