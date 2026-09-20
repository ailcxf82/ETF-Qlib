# 真实 ETF 数据源语义证据

用户已确认数据来源为 Tushare；后续保留接口和下载参数、导出映射及必要正确性检查，不逐条跨来源复验，也不重新运行下述已通过核对。下述全量报告作为已有证据复用，不要求为每次研究重新制作来源档案。

2026-09-13 完成全量只读 CSV/bin 字段身份核对。证据 ID：`127efea309e9a9741e57f8893d33632c9f8a8b2947db8f34d1ebfd15dbcd4199`，完整报告位于 `artifacts/data_evidence/<evidence_id>/export_semantics.json`。这是字段语义证据，G0 尚未通过。

## 核对范围和结果

- 原始 bin：`D:/qlib_data/etf_qlib_data`；本地 CSV：`D:/qlib_data/etf_csv_data`。
- 覆盖原始 bin 池全部1,769只 ETF，1,769只字段匹配；有效 OHLC 报价1,335,742行。
- 按旧日历与各标的有效区间解码 bin，逐字段与 CSV 转换后的 float32 精确比较，缺失值单独比较；字段为 OHLC、pre_close、change、pct_chg、vol、volume、amount。
- 按官方量额单位检查成交均价是否落在原始高低价附近，5%容差内单位关系失败数为0；这不替代事件、价格异常和字段覆盖准入检查。
- 保存全部原始文件与 CSV 哈希，运行前后核对一致；不重写 bin、日历或 CSV，不调用行情 API、LLM 或读取凭据。
- 导出源码：`D:/quant_project/qlibQuantData/tushare_etf_to_qlib.py`，SHA256为 `9481fce2f5ee76ce2b42994e3d96188517b3555d88775e36a8a50fbcb3f63883`。

## 已有导出与内部映射

| 本地字段 | 原始来源/单位 | 内部处理 |
| --- | --- | --- |
| open/high/low/close | fund_daily，元；现有路径未进行复权转换 | 映射原始价格；研究价格仍依赖独立复权证据 |
| volume | 导出器原样复制 vol，手 | ×100 转份额；不是已经转换后的份额 |
| amount | 原样导出，千元 | ×1000 转人民币金额 |
| change | 涨跌额，元 | 不作为 return_1d 或 Qlib $change |
| pct_chg | 涨跌幅，百分数 | ÷100 后作为 return_1d/Qlib $change |
| pre_close | 上游昨收字段，元 | 原样保留，企业行为和除权参考关系仍需核验 |
| factor | 当前导出 FEATURE_FIELDS 不含该字段 | 不填1；必须补齐真实复权与企业行为证据 |

字段单位依据 [Tushare fund_daily 官方定义](https://tushare.pro/document/2?doc_id=127)，并由本地导出源码和全量 CSV/bin 一致性核对支持。原价口径是根据现有未转换的 fund_daily 导出路径作出的推断；不能据此假定已处理历史分红拆分。

供后续准入使用的配置为 `configs/data/tushare_export_evidence.yaml`。它是独立可选配置，不改变默认配置；日历、PIT 元数据和基准仍为空，factor字段仍缺失，因此不能用它跳过 G0。

## 尚未证明的 G0 条件

1. 沪深两市年度公告与2020春节临时公告已独立核对并归档，见下节；原日历340个休市日期需要在新快照重编码时排除，不能直接编辑 day.txt。日期位置证据不自动证明所有调仓日的历史信息可用性。
2. 当前导出缺少复权因子与完整企业行为映射，需要补齐真实来源；没有读取或构造付费接口数据。[基金复权字段定义](https://tushare.pro/document/2?doc_id=199)
3. 当前 enriched/history 元数据的 metadata_asof 为当前查询时点，不能证明历史分类和运营状态；例如“纯境内”字段可包含跟踪港股的产品，仍须按实际资产和时点证据分类。
4. 沪深300价格指数完整报价及其身份、覆盖、估值时点尚未提供；股票池 csi300.txt 不等于指数行情。
5. K/流动性已由用户委托常规设定，最大回撤12%已确认；LLM费用已授权无上限，最新佣金0.03%，暂不另加最低费用；正式研究协议应在结果观察前冻结。

## 重现

```powershell
python scripts/verify_tushare_export_evidence.py
```

默认核对全部 bin 成员；`--limit N` 仅生成明确标记的有限样本证据。报告列出每只标的的解码行数、报价行数、字段差异、量额关系失败、factor文件状态及 CSV 中不属于 bin 成员日历的行数，不能把范围外行或 NAV 行混入有效报价结论。

## 沪深交易日历证据

2026-09-13 保存了沪深两市2020–2026年度休市公告，以及两市2020-01-27春节延长休市公告。两市逐年休市区间经独立提取后完全一致。完整组合证据ID为 `b2e86cde671f974ee89f4f7e9f982d86e50d9c430fae4ad1c767a8c9b325ea4c`；目录 `artifacts/data_evidence/<evidence_id>/` 保存公告HTML、生成脚本副本、所有文件哈希、日历哈希及 `calendar_evidence.json`。

- 日历文件 `trusted_calendar.txt` 覆盖2020-01-02至2026-12-31，共1,697个交易日；2026未来日期来自已公布年度安排，发生新公告时仍须更新。
- 原始数据日历覆盖2020-01-01至2026-09-04；对应可信交易日1,619日，全量bin有效报价也覆盖1,619个不同日期，1,335,742行报价无休市日冲突。
- 原日历340个休市日期由309个周末及31个工作日休市组成。该核对继承不可变SSE全量报价证据，并再次核对全部原始文件哈希一致。
- SSE全量报价日期核验父证据ID为 `64e98e53a8f914dd7e8a1124f837355003aca5797e890d397c8ecbb686ba2f21`。组合脚本验证父报告身份、全部公告哈希、逐年区间、临时公告及日历内容，不仅比较文件名或日期数。
- 14项确定性测试覆盖跨年、单日休市、复市日期排除、调休周末、异常区间、公告日期、嵌套网页、幂等发布、文件损坏、中断后恢复、路径越界及可选配置保持G0阻断；CI不访问交易所网站。

2020春节延期是历史时点边界：年度原安排1月31日开市，1月27日公告才将休市延长至2月2日。因此事后日历中1月23日成为当月最后交易日，不能据此声称1月22日已知道这个月末节点。该证据只验证实际日期位置与已公布安排；正式调仓仍须证明决策时点的日历可用性，或排除受影响区间。[上交所临时公告](https://www.sse.com.cn/disclosure/announcement/general/c/c_20200127_4991582.shtml)、[深交所临时公告](https://www.szse.cn/disclosure/notice/t20200127_573917.html)

可选配置 `configs/data/tushare_calendar_evidence.yaml` 合并已核对量额单位及独立日历，保留缺失factor、PIT元数据和沪深300行情为阻断项；默认项目配置仍保持冻结前状态。原始日历/bin未修改，也未生成合格真实快照或执行正式回测。

重现公开公告证据（无行情API、付费调用或凭据读取）：

```powershell
python scripts/build_sse_calendar_evidence.py
python scripts/build_exchange_calendar_evidence.py --sse-evidence artifacts/data_evidence/64e98e53a8f914dd7e8a1124f837355003aca5797e890d397c8ecbb686ba2f21
python -m pytest -q tests/unit/test_calendar_evidence.py --junitxml=artifacts/tests/calendar.xml
```

年度公告网页内容发生变化时会生成新的证据身份；不得把新的下载覆盖进旧身份目录。组合证据通过文件锁、临时目录和目录原子发布保存成功版本；中断临时目录不能被当成完成证据。


2026-09-13 后续证据修订：159919旧网页JSON仅保存抓取超时，不能证明公告正文。现已取得嘉实2021-08-28原始收益分配PDF，明确登记09-10、除息09-13、派息09-14及0.70元/10份；[分红证据v2](../artifacts/data_corrections/dividend_exchange_v2/assembly_report.json)替换绑定，现金/日期/保留源行未变，真实关系复查通过。原证据文件和历史诊断保留；完整当前缺口见[08](08-first-loop-readiness.md)。
