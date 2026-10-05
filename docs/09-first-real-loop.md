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

运行期间可在另一个终端查看结构化进度：

```powershell
E:/tools/anaconda/envs/qlib_zhengshi/python.exe -m etf_ml.cli monitor-run --config configs/data/tushare_diagnostic_first_loop.yaml --run-id <run-id>
```

命令持续输出变化的 `progress.json` 状态，运行结束后退出；`--once` 只读取当前状态，`--path <run-directory>` 可直接监控配对或 worker 目录。每层的 `progress.jsonl` 保留追加事件历史，`progress.json` 是原子更新的最新状态。新代码只记录新启动运行的事件，不回填旧运行。

若已确认主进程及其子进程均已退出，使用以下命令把被人工停止的运行记录为终态；`--process-exited` 防止在仍运行时误写取消状态：

```powershell
E:/tools/anaconda/envs/qlib_zhengshi/python.exe -m etf_ml.cli cancel-run --path artifacts/diagnostic_first_loop/runs/<run-id> --process-exited --reason operator_stop
```

闭环完成要求至少三个真实付费传输的响应证据、一个已完成的试验、基线训练结果，以及全部候选成功进入评估。候选被拒绝或证据不足也可构成技术闭环；代码或模型执行失败不会被误报为成功。账单接口未返回实际费用时明确记录未知，不据此报告零费用。

## 可审阅证据

- 输入装配：`artifacts/diagnostic_first_loop/inputs/assembly_report.json`
- LLM发送内容：`artifacts/diagnostic_first_loop/llm_transmission_review.md`
- 智谱目的地授权：`artifacts/diagnostic_first_loop/llm_destination_authorization.json`
- 运行进度/结果：`artifacts/diagnostic_first_loop/runs/first-real-loop-20260914/first_loop_progress.json` / `first_loop_report.json`
- 回归：`artifacts/tests/diagnostic_first_loop_final_regression.xml`

实际状态以运行结果为准；本文件不预先声称闭环已完成。

## 2026-09-20：真实运行证据与下一轮优化

### 已验证的运行链路

- v10 已实际完成 3 次付费、可审计的 GLM 响应，并复用了一个已成功的五折基线。它没有产生候选评估：模型首次提案把 `factors` 放入示例包装字段，修复响应又返回裸因子对象；转换器因此正确拒绝，而没有静默放宽契约。v10 的 `first_loop_report.json` 为 `incomplete`，不能作为闭环成功证据。
- 随后将提案提示词和修复提示词统一为唯一输出包络 `{"factors":[{...}]}`，并明确要求数组恰有一项；保留转换器的严格校验。相关单元、预算、诊断和基线复用测试共 38 项通过。
- v11 使用相同的哈希校验基线重新开始。三个真实 LLM 响应已经完成；`gap_normalized-v1` 的代码、隔离材料化和质量工件已写入本地注册表。v11 已停止；只有 `first_loop_report.json` 终态为 `completed`，且候选具有完整评估报告时，才可声称技术闭环完成。

### 优化方向（不削弱研究门槛）

1. **复用同一因子试验内的等价对照（已实现，待 Docker 集成回归）。** 单新因子时 `ablation` 与 baseline 的特征集相同，现按特征集 ID 引用已完成的 baseline 子运行，并继续由协议、快照、种子和子运行 manifest 哈希验证；报告记录 `reused_from=baseline`。不同的多因子组消融仍独立训练。
2. **将“因子数=1”的简化限定为首轮。** 首轮只生成一个因子以降低模型输出和诊断面；多因子后续试验才执行真正的组消融。这样不会把单因子基线复用误当作多因子组消融证据。
3. **把候选材料化后的配对运行做可恢复检查点。** 每个 `kind × seed` 子运行完成即已由 RunStore 固化；在总控制器层记录完成矩阵，可在中断后直接跳过经 manifest 验证的子运行，避免重复长回测。
4. **保留两层 LLM 修复，但压缩上下文。** 已证明严格 JSON 包络可捕获无效输出。后续应把完整场景描述改为哈希绑定的紧凑字段/约束摘要，保留审计内容在本地，减少请求延迟与不稳定性，不能省去因果与字段契约。
5. **将回测进度写为折、种子和实验类型的结构化状态。** 当前日志可审阅但不便快速判断进度；增加不可变进度清单可让监控只报告有实质变化，并为估时和卡死诊断提供证据。

上述优化在 v11 终态后再实施和回归；不会改变 v11 的结果，也不会把诊断研究提升为正式 G0 或投资验收。

## 2026-09-21：v20 运行中观察与后续验收

v20 使用已验证的 Docker 运行时和同一诊断快照。三个真实 LLM 调用均已完成，候选
`overnight_return_mean_5` 已通过材料化与质量门，随后进入配对研究。以下观察来自仍在运行的
首个基线种子，不能替代终态报告：

- A、B 两折的等权池辅助基线各约用 4–5 分钟；模型训练和动量辅助基线显著更短。
- 等权池并未停滞：基础回测工件先落盘，随后运行 2 倍成本压力情景并写入独立目录。旧事件流在这两个
  子阶段之间没有事件，因而会表现为数分钟的静默。
- 辅助策略缓存按快照、协议、折、策略、执行/成本/标的池政策、评估日期哈希和代码哈希绑定。首个种子
  创建缓存；相同折的后续种子与候选会复用已完整写入 `metrics.json`、基准日收益和压力日收益的条目。

运行结束后按以下顺序实施并验收优化：

1. **细化回测阶段事件。** 在 `evaluate_with_stress` 周围记录基础回测开始/完成、每个成本压力开始/完成、
   工件持久化完成和缓存命中。验收：监控在任何单次回测中都能显示当前情景，且事件时间单调、不会把未完成
   工件当作缓存命中。
2. **同步根进度。** 将嵌套 `paired/progress.json` 的折、种子、类别、策略和缓存状态原子汇总到
   `first_loop_progress.json`。验收：仅监控根目录即可显示全链路当前阶段；嵌套任务失败或取消后根状态同步终态。
3. **减少缓存命中前的无效工作。** 目前 `run_auxiliary` 在检查缓存前即构造完整动量分数。将策略的分数构造
   移入未命中路径，并保持缓存身份及信号覆盖审计不变。验收：缓存命中不调用 `momentum_scores`，返回的
   指标、日收益哈希和工件引用保持一致。
4. **复用特征准备而非放松验证。** 将同一试验共享的、哈希校验后的 `FeatureArtifact` 显式传递给各个
   配对子运行，避免重复材料化百万级面板；训练、回测和每个子运行的 manifest 仍独立。验收：材料化次数
   降低，任一输入哈希变化仍导致拒绝复用。
5. **对跨运行缓存保持保守。** 仅在把当前全局 `code_hash` 收窄为精确依赖清单、并补齐全工件校验后，才考虑
   跨已取消运行复用。当前 v20 只在同一运行内复用，避免因无关源码变更或不完整工件污染研究证据。

### 优化测试计划

| 层级 | 验证内容 | 通过条件 |
| --- | --- | --- |
| 单元 | 进度事件顺序、根进度汇总、终态传播 | 基础/压力/持久化事件完整，取消与异常不被改写为完成 |
| 单元 | 辅助缓存命中与未命中 | 命中不执行分数构造；缺少 manifest、指标或任一日收益文件时强制重算 |
| 集成 | 三个种子、五个开发折的配对运行 | 第一个种子创建缓存，后续相同折与候选复用；日收益哈希、指标和协议引用一致 |
| 集成 | Docker 因子与特征共享 | 输入相同只材料化一次；因子、快照、协议或依赖哈希变化时拒绝复用 |
| 操作验收 | `monitor-run` 只监控根运行目录 | 连续显示试验、类别、种子、折、策略、基础/压力情景和缓存命中；终态自动退出 |
| 回归 | 既有诊断首轮、缓存、LLM 传输和因子容器测试 | 全部通过，且诊断状态、2026 留出隔离、正式 G0/投资验收禁令均保持不变 |

在 v20 产生终态报告前，不运行会改变 Python 源码哈希的优化实现或回归；届时先保存并审阅该运行的
报告、评估和 LLM 审计工件，再用新的冻结协议执行测试。

## 2026-09-21：v20 终态与已实施优化

`first-real-loop-20260921-v20` 已完成一个真实诊断闭环：复用的五折基线、三次付费 LLM
传输、一个候选因子的 Docker 因果材料化、三随机种子五开发折配对评估、消融和反馈均有落盘工件。
候选 `overnight_return_mean_5` 被拒绝，原因为未由消融确认、超过绝对及成本压力风险限制、回撤和换手恶化、
多数折未增益且随机种子不稳定。拒绝是有效研究结论，不是执行失败。

本次仍是诊断研究：终态报告明确为 `formal_g0_passed=false`、`holdout_evaluated=false`、
`investment_accepted=false`。因此不据此给出 ETF 投资推荐或正式 G0 结论。

已实施并在单元回归中验证的优化：

1. `run_auxiliary` 先验证缓存清单、指标和基准/压力日收益工件，只在未命中时构造动量或等权分数；
   缓存身份与输出内容不变。
2. `evaluate_with_stress` 公开基础回测和每个成本压力情景的开始/完成回调，辅助运行将其写入结构化进度流。
   监控可据此区分正常长回测和停滞。

回归命令：

```powershell
E:/tools/anaconda/envs/qlib_zhengshi/python.exe -m pytest tests/unit/test_auxiliary_cache.py tests/unit/test_progress_monitor.py tests/unit/test_research_code.py tests/unit/test_budget_llm.py -q
E:/tools/anaconda/envs/qlib_zhengshi/python.exe -m pytest tests/integration/test_rdagent_research.py -q
```

结果：单元回归 `36 passed`，RDAgent 集成回归 `1 passed`。集成测试还验证了本地 replay 缺少响应时会在
账单预留前失败，且静态拒绝的未来函数不会写入可复用因子注册表。下一次真实运行须使用新的代码哈希和新的
冻结协议；v20 的产物保持原样，继续作为本次实现前的可审计基准。
