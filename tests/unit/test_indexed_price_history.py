import numpy as np
import pandas as pd
import pytest
from etf_ml.data.actions import raw_close_as_of, price_histories
from etf_ml.errors import QualityError


def test_indexed_valuation_matches_direct_history_for_stale_actions(panel, calendar):
    instrument = "510300.SH"
    panel = panel.copy()
    panel.loc[(calendar[100:], instrument), "raw_close"] = np.nan
    events = pd.DataFrame([{ "event_id":"split", "datetime":calendar[120],
        "instrument":instrument, "cash_per_share":0., "share_multiplier":2., "sequence":0},
        {"event_id":"cash", "datetime":calendar[130], "instrument":instrument,
        "cash_per_share":0.02, "share_multiplier":1., "sequence":0}])
    histories = price_histories(panel)
    for day in calendar:
        for symbol in panel.index.get_level_values("instrument").unique():
            assert raw_close_as_of(panel, events, symbol, day, histories=histories) == raw_close_as_of(panel, events, symbol, day)
    with pytest.raises(QualityError, match="no raw valuation"):
        raw_close_as_of(panel, events, "unknown", calendar[-1], histories=histories)


def test_indexed_valuation_cannot_use_future_first_quote(panel, calendar):
    panel = panel.copy()
    panel.loc[(calendar[:50], "510300.SH"), "raw_close"] = np.nan
    with pytest.raises(QualityError, match="no usable"):
        raw_close_as_of(panel, None, "510300.SH", calendar[49], histories=price_histories(panel))


def test_exchange_quotes_use_raw_snapshot_without_provider_reload(panel, calendar, monkeypatch):
    import qlib
    from qlib.constant import REG_CN
    from etf_ml.backtest.qlib_runner import ETFExchange
    from etf_ml.contracts import PortfolioPolicy
    qlib.init(region=REG_CN, kernels=1)
    monkeypatch.setattr("qlib.backtest.exchange.D.features", lambda *a,**kw: (_ for _ in ()).throw(AssertionError("provider must not reload")))
    exchange = ETFExchange(panel, PortfolioPolicy(minimum_commission=0), start_time=calendar[120], end_time=calendar[-1])
    assert len(exchange.quote_df) == 60 * 3
    assert exchange.quote_df["$factor"].eq(1).all()
    assert exchange.quote_df.loc[("510300.SH", calendar[130]), "$close"] == panel.loc[(calendar[130], "510300.SH"), "raw_close"]
    from qlib.backtest.decision import Order
    assert exchange.is_stock_tradable("510300.SH", calendar[130], calendar[130], Order.BUY)
    history = panel.xs("510300.SH", level="instrument").loc[:calendar[130]].iloc[:-1].amount_currency.tail(20)
    assert exchange.historical_amount("510300.SH", calendar[130]) == float(history.mean())


@pytest.mark.parametrize("column", ["tradable", "buyable", "sellable", "suspended", "volume_shares"])
@pytest.mark.parametrize("value", [0., 1., np.nan])
def test_scalar_permissions_match_dataframe_and_observe_mutation(column, value):
    from etf_ml.data.normalize import row_trading_permissions, trading_permissions
    row = pd.DataFrame({"tradable":[1.], "quoted":[True], "volume_shares":[100.],
        "buyable":[1.], "sellable":[1.], "suspended":[0.]})
    row.loc[0,column] = value
    assert row_trading_permissions(row.iloc[0]) == trading_permissions(row).iloc[0].to_dict()
