import pandas as pd
import pytest
from qlib.backtest.position import Position

from etf_ml.backtest.metrics import position_exposures
from etf_ml.errors import QualityError


def universe():
    dates = pd.bdate_range("2023-01-02", periods=3)
    index = pd.MultiIndex.from_product([dates, ["A", "B"]], names=["datetime", "instrument"])
    data = pd.DataFrame({"tracking_group": ["X", "X", None, None, "Y", "X"]}, index=index)
    return dates, data


def holding():
    return Position(cash=10, position_dict={"A": {"amount": 40, "price": 1},
                                           "B": {"amount": 10, "price": 5}})


def test_actual_qlib_position_values_groups_and_cash_reconcile_without_future_classification():
    dates, data = universe()
    metrics, rows = position_exposures({dates[0]: holding(), dates[1]: holding()}, data)
    assert metrics["max_single_weight"] == .5
    assert metrics["max_group_weight"] == .9
    assert metrics["mean_cash_weight"] == .1
    assert metrics["max_unclassified_weight"] == 0
    assert metrics["max_holding_count"] == 2
    assert rows.loc[dates[1], "group_count"] == 1
    assert list(rows.index) == list(dates[:2])


def test_unknown_groups_are_aggregated_and_reported_as_unclassified_exposure():
    dates, data = universe()
    data.loc[:dates[1], "tracking_group"] = None
    metrics, rows = position_exposures({dates[0]: holding()}, data)
    assert metrics["max_group_weight"] == .9
    assert metrics["max_unclassified_weight"] == .9
    assert rows.group_count.iloc[0] == 1


@pytest.mark.parametrize("quantity,price,cash", [(-1, 1, 10), (1, -1, 10), (1, 1, -1)])
def test_invalid_account_values_cannot_produce_exposure_success(quantity, price, cash):
    dates, data = universe()
    position = Position(cash=cash, position_dict={"A": {"amount": quantity, "price": price}})
    with pytest.raises(QualityError):
        position_exposures({dates[0]: position}, data)
