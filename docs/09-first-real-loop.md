# 首轮真实数据研发闭环

本轮采用诊断模式，复用 Tushare 真实行情和已有缓存。它用于验证 RDAgent 提案、编码、隔离执行、Qlib 训练、配对回测、消融及反馈；不代表正式 G0 或投资验收通过。

## 已处理的入口阻塞

- `DataSpec.mode=diagnostic` 明确允许当前成员分类作历史回溯假设，保留真实刷新时间；没有伪造历史公开时间。默认 `formal` 仍执行历史可用时间检查。
- 保留原始 provider 的全部 1769 个标的及元数据。当前股票基金分类结合境外指数名称、QDII 标记筛选国内股票 ETF；其他/未知类别仍保留在源面板。
- 从已有、哈希验证的 Tushare 缓存装配 810 条现金分红，合并 144 条已有原始公告证据支持的份额转换。
- 三只货币 ETF 的无法映射收益记录完整记录在装配报告。本轮股票 ETF 投资范围不使用它们；未将这些记录伪装成普通分红。
- 诊断模式保留事件/复权一致性报告和失败状态；结构、单位、原始数据完整性及成员覆盖检查仍执行。
- 诊断快照与正式配置不能交叉训练；诊断研究不能冻结为正式特征或进入正式留出验收。研究与模型产物位于独立的 `artifacts/diagnostic_first_loop`。

## 固定实验设置

沪深300价格指数、50万元资金、单侧佣金0.03%、滑点0.03%、最低佣金0、前5%等权、20日ADV参与上限20%、最大回撤阈值12%、月中/月末调仓，标签为5日收益。保留默认五个开发折和三个随机种子；2026年留出数据不用于首轮研发。

首轮固定 LightGBM，200轮上限、30轮早停、2线程。研发最多一个试验，每个试验1至3个因子；完整三模型对比属于后续正式研发矩阵。LLM费用无上限，仍受时间、内存、取消和输出大小限制。

## 运行方式

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONPATH=(Get-Location).Path+'\src;'+(Get-Location).Path
E:/tools/anaconda/envs/qlib_zhengshi/python.exe scripts/prepare_diagnostic_first_loop.py
E:/tools/anaconda/envs/qlib_zhengshi/python.exe -m etf_ml.cli first-loop --config configs/data/tushare_diagnostic_first_loop.yaml --run-id first-real-loop-20260914
```

执行顺序：诊断快照 → 真实基线 → RDAgent假设/提案/代码 → Docker因果性检查 → 配对训练、成本压力、因子/组消融 → 注册研究决策 → 本地反馈 → 闭环报告。

闭环完成要求至少三个真实付费传输的响应证据、一个已完成的试验、基线训练结果，以及全部候选成功进入评估。候选被拒绝或证据不足也可构成技术闭环；代码或模型执行失败不会被误报为成功。账单接口未返回实际费用时明确记录未知，不据此报告零费用。

## 可审阅证据

- 输入装配：`artifacts/diagnostic_first_loop/inputs/assembly_report.json`
- LLM发送内容：`artifacts/diagnostic_first_loop/llm_transmission_review.md`
- 智谱目的地授权：`artifacts/diagnostic_first_loop/llm_destination_authorization.json`
- 运行进度/结果：`artifacts/diagnostic_first_loop/runs/first-real-loop-20260914/first_loop_progress.json` / `first_loop_report.json`
- 回归：`artifacts/tests/diagnostic_first_loop_final_regression.xml`

实际状态以运行结果为准；本文件不预先声称闭环已完成。
