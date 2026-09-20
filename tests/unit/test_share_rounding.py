import pytest

from etf_ml.backtest.accounting import Account
from etf_ml.data.actions import adjusted_shares
from etf_ml.errors import QualityError


@pytest.mark.parametrize("quantity,multiplier,mode,expected", [
    (100., .49977589, "ceil", 50.), (100., .49977589, "floor", 49.),
    (100., .49977589, "none", 49.977589), (0., .49977589, "ceil", 0.),
    (10., .1, "ceil", 1.), (150.5, 1.5, "none", 225.75),
])
def test_share_conversion_rounding_is_distinct_from_trade_lots(quantity, multiplier, mode, expected):
    assert adjusted_shares(quantity, multiplier, mode) == pytest.approx(expected)


@pytest.mark.parametrize("mode", ["ceil", "floor"])
def test_account_records_share_rounding_and_rejects_changed_retry(mode):
    account = Account(20., shares={"A":100.}, marks={"A":10.})
    before = account.equity()
    account.apply_event("merge", "A", share_multiplier=.49977589, share_rounding=mode)
    quantity = 50. if mode == "ceil" else 49.
    assert account.cash == 20.
    assert account.shares["A"] == quantity
    change = (quantity - 100. * .49977589) * 10. / .49977589
    assert account.equity() == pytest.approx(before + change)
    assert account.events[0]["rounding_shares"] == pytest.approx(quantity - 100. * .49977589)
    account.apply_event("merge", "A", share_multiplier=.49977589, share_rounding=mode)
    assert len(account.events) == 1
    with pytest.raises(QualityError, match="identity changed"):
        account.apply_event("merge", "A", share_multiplier=.49977589, share_rounding="none")


def test_zero_and_fractional_floor_conversion_does_not_create_phantom_holdings():
    account = Account(20., shares={"A":.5}, marks={"A":10.})
    account.apply_event("merge", "A", share_multiplier=.5, share_rounding="floor")
    assert "A" not in account.shares
    empty = Account(20.)
    empty.apply_event("merge", "A", share_multiplier=.5, share_rounding="ceil")
    assert not empty.shares and empty.equity() == 20.


@pytest.mark.parametrize("mode", ["lot", "nearest", None])
def test_unknown_share_rounding_is_rejected(mode):
    with pytest.raises(QualityError):
        adjusted_shares(100., .5, mode)
