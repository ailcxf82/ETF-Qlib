# LightGBM 训练诊断与有限参数预筛

日期：2026-09-30。范围：训练证据和研发 early_stop 参数预筛。

## 已实现

`models/train.py` 对每次 LightGBM 训练建立独立的 `evals_result`，不修改调用方配置。
`models/diagnostics.py` 从 LightGBM 原生模型导出读取实际生效参数，包含框架默认值和别名解析结果。
模型 manifest 的 `training` 新增：

- `effective_parameters`：原生参数快照；不是仅保存用户传入的 constructor。
- `evaluation_history`：train/valid 的每轮指标，含最佳轮数之后的早停等待阶段。
- `evaluated_rounds`：实际评估轮数；`retained_rounds`：模型保留轮数。
- `iteration_cap`、`early_stopping_rounds`、`stop_reason`：生效预算和停止状态。
- `diagnostics`：首轮最佳、达到轮数上限、训练误差下降而验证误差上升等描述性标记；缺失或非有限曲线标为 partial。

原 `trained_rounds` 字段继续表示保留轮数，schema 仍为 1。历史 manifest 不回填、不重写。
诊断不修改训练目标、默认超参数、早停评价标准或预测。

源码变化会改变已有的全工程 code hash。旧模型/协议的严格版本检查仍然有效，不应修改旧 hash 来继续使用旧身份；新训练绑定新源码。

## 有限预筛入口

```powershell
$env:PYTHONPATH = 'D:\quant_project\ETF-Qlib\src'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'E:\tools\anaconda\envs\qlib_zhengshi\python.exe' -m scripts.screen_lgbm_parameters `
  --config configs/data/tushare_formal_first_loop.yaml `
  --snapshot artifacts/formal_tushare/data/8b384b5d9f8fa10d3d7b6aebbf11fd9463dc76cc9fe203d38dfc6f5ad4322831 `
  --plan configs/research/lightgbm_tuning_screen.yaml `
  --output E:/quant_runs/ETF-Qlib/lgbm-tuning-20260930/screen-v1
```

输出目录必须不存在。失败也保留已完成的行、模型和原因；再次运行需新目录。入口不会自动续跑、扩预算或激活模型。
2026-09-30 本机 D 盘空间不足，本轮测试与实验产物改存 E 盘，未删除历史研究产物。

计划固定一个种子 42、五折、最多 20 次模型拟合，每次最多 500 轮、50 轮早停、2 线程。
四个版本为 baseline、shallow（7 叶/深度 3）、larger_leaf（最小叶子样本 1000）、sampled（特征和样本均 80%、每轮抽样）。
baseline 明确列出完整起始参数；不会自动读取或依赖 `configs/models/lightgbm.yaml`。
所有版本使用同一套 20 项基线特征、同一收益标签、相同学习样本和时间折。叶子与深度属于同一结构变化；采样版同时调整行列采样，是组合候选，不用于单独归因二者。

## 数据边界与证据

- 开始前验证预算、配置边界及三个输入的 hash：`research/panel.parquet`、`calendar.txt`、`universe.parquet`。
- 只加载 research 行情以及边界前的 universe；日历裁剪至 holdout 前。不打开 `holdout/` 文件，也不反序列化历史模型。
- 保持快照的 holdout 边界与股票池政策；继续使用按标签成熟时间剔除跨段样本的 builder。
- 模型拟合仅用 train；早停和参数预筛指标都来自 early_stop（Qlib valid）。selection 不生成评价指标，组合回测不执行。
- early_stop 同时用于选轮数和比较参数，存在选择偏差。报告不能当作独立泛化结论。
- 保留输入快照的资格状态。G0 未通过时允许输出明确的开发诊断，不因此升级数据资格或投资验收。
- 运行中源码改变则失败；结束时复核输入 hash。输出 protocol 保存源码、环境、输入、配置、计划、特征身份。

每个模型独立保存模型包、训练曲线和生效参数；每个 fold/seed/variant 保存验证预测及文件 hash。
`report.json` 保存逐次结果和完成进度；`report.md` 解释最佳轮数、实际轮数、验证 MSE、RankIC。
汇总使用同折同种子基线的 MSE 比值和 RankIC 差值，不跨时期直接平均不同尺度的原始 MSE。
未知 RankIC 不作为改善，样本索引不一致不能比较。没有自动赢家或 accepted 状态。

## 验证

定向回归覆盖：Ridge/LightGBM/XGBoost 保存加载与最新推断；LightGBM 早停后丢弃轮数的完整记录；
原生参数别名和 fit 预算覆盖；新增诊断与原模型预测逐值一致；独立曲线不污染调用方配置；
预算预检、参数白名单、缺失 RankIC、样本不一致、输入篡改、holdout 边界改变、结果不可覆盖，以及完整小型参数预筛。

2026-09-30：40 passed，7 条既有 MLflow 文件存储弃用告警。报告：
`E:/quant_runs/ETF-Qlib/lgbm-tuning-20260930/test-v2.xml`。
首轮因 D 盘满与测试临时目录位于 MLflow 保留的 artifacts 祖先下失败，失败证据保留于同目录的 `failed-test-v1.xml`；换用 E 盘普通临时目录后通过。

## 后续验收

少量候选应另行冻结，用多种子和 selection 做完整比较，再检查扣费收益、回撤、换手和压力情景。
独立 holdout 不进入参数筛选。当前参数预筛不改变正式研究 campaign、因子接受状态或每日模型版本。

## 首轮真实研发数据结果

2026-09-30：`screen-v1` 已完成 20/20 次训练，进程退出 0。使用上述冻结快照的研究视图、20 项基线特征、h=5、seed=42。

| 版本 | RankIC 改善折数 | RankIC 增量中位数 | 相对基线 MSE 比值中位数 |
| --- | ---: | ---: | ---: |
| shallow | 2/5 | -0.005309 | 0.999850 |
| larger_leaf | 4/5 | +0.001553 | 1.000568 |
| sampled | 2/5 | -0.020412 | 0.999236 |

`larger_leaf` 是值得后续检验的候选；排序改善幅度小，MSE 略差，尚不升级默认模型。
浅树及采样版本在本次验证段比较中没有稳定排序增益。降低 MSE 不必然改善 ETF 排序。

完整证据：`E:/quant_runs/ETF-Qlib/lgbm-tuning-20260930/screen-v1/report.md`、`report.json`、`protocol.json`。
20 份模型、预测文件 hash、训练曲线长度、最佳轮数对应的预测 MSE、源码身份已核对；`closure.json` 保存核对记录。
对应 113 个源码文件复制到同目录 `source_snapshot/etf_ml/`，可恢复本次实验实现。
本轮未评价 selection/组合收益/holdout，原快照 `formal_g0_passed=false` 保持不变。
