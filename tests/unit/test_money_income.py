import numpy as np
import pandas as pd
import pytest
from etf_ml.data.money_income import (money_income_intervals, money_income_coverage,
    require_daily_money_income, validate_money_income_intervals)
from etf_ml.errors import QualityError


def source():
    return pd.DataFrame([
        {"FSRQ": "2024-06-17", "DWJZ": "0.4100", "SDATE": "", "NAVTYPE": "1", "LJJZ": "99999"},
        {"FSRQ": "2024-06-16", "DWJZ": "0.8114", "SDATE": "2024-06-15", "NAVTYPE": "0", "LJJZ": "-99999"},
    ])


def normalized(raw=None):
    return money_income_intervals(source() if raw is None else raw, "159001.SZ",
                                  basis_shares=100, par_value=100)


def test_holiday_income_keeps_amount_and_interval_without_daily_inference():
    raw=source();before=raw.copy(deep=True);frame=normalized(raw)
    pd.testing.assert_frame_equal(raw,before)
    assert frame.income_per_basis.tolist()==[.8114,.4100]
    assert frame.income_per_share.tolist()==pytest.approx([.008114,.0041])
    assert frame.period_start.tolist()==[pd.Timestamp("2024-06-15"),pd.Timestamp("2024-06-17")]
    assert frame.index.get_level_values("datetime").tolist()==[pd.Timestamp("2024-06-16"),pd.Timestamp("2024-06-17")]
    assert len(frame)==2 and frame.attrs["daily_income_inferred"] is False
    # 100 shares earn the observed 1.2214 yuan across these records; not
    # twice the weekend aggregate and not a seven-day annualized yield.
    assert (frame.income_per_share*100).sum()==pytest.approx(1.2214)
    report=money_income_coverage(frame,"2024-06-15","2024-06-17")[0]
    assert report["covered_calendar_days"]==3 and report["calendar_coverage_complete"]
    assert report["aggregate_intervals"]==1 and not report["daily_amounts_complete"]
    with pytest.raises(QualityError,match="Aggregate income"):
        require_daily_money_income(frame)


def test_negative_and_zero_income_are_not_filtered_and_daily_data_remains_daily():
    raw=pd.DataFrame([
        {"FSRQ":"2024-06-15","DWJZ":"-0.0100","SDATE":"","NAVTYPE":"1"},
        {"FSRQ":"2024-06-16","DWJZ":"0.0000","SDATE":"","NAVTYPE":"1"},
        {"FSRQ":"2024-06-17","DWJZ":"0.4228","SDATE":"","NAVTYPE":"1"}])
    frame=money_income_intervals(raw,"511960.SH",basis_shares=100,par_value=100)
    assert frame.income_per_basis.tolist()==[-.01,0,.4228]
    assert require_daily_money_income(frame) is frame
    assert money_income_coverage(frame,"2024-06-15","2024-06-17")[0]["daily_amounts_complete"]


def test_missing_day_is_reported_without_forward_filling_income():
    raw=source().iloc[[0]];frame=normalized(raw)
    report=money_income_coverage(frame,"2024-06-15","2024-06-17")[0]
    assert report["missing_calendar_days"]==2
    assert report["missing_examples"]==["2024-06-15","2024-06-16"]
    assert not report["daily_amounts_complete"] and len(frame)==1


@pytest.mark.parametrize("mutation",["missing_start","unknown_kind","daily_interval","reversed",
    "overlap","duplicate","bad_day","missing_income","infinite","boolean"])
def test_ambiguous_or_corrupt_source_is_rejected(mutation):
    raw=source()
    if mutation=="missing_start":raw.loc[1,"SDATE"]=""
    elif mutation=="unknown_kind":raw.loc[1,"NAVTYPE"]="2"
    elif mutation=="daily_interval":raw.loc[0,"SDATE"]="2024-06-17"
    elif mutation=="reversed":raw.loc[1,"SDATE"]="2024-06-18"
    elif mutation=="overlap":raw.loc[0,"NAVTYPE"]="0";raw.loc[0,"SDATE"]="2024-06-16"
    elif mutation=="duplicate":raw=pd.concat([raw,raw.iloc[[0]]],ignore_index=True)
    elif mutation=="bad_day":raw.loc[0,"FSRQ"]="2024-02-30"
    elif mutation=="missing_income":raw.loc[0,"DWJZ"]=None
    elif mutation=="infinite":raw.loc[0,"DWJZ"]="Infinity"
    else:raw.loc[0,"DWJZ"]=True
    with pytest.raises(QualityError):normalized(raw)


@pytest.mark.parametrize("basis,par",[(0,100),(-100,100),(True,100),(100.5,100),(100,0),(100,np.inf)])
def test_units_must_be_explicit_and_valid(basis,par):
    with pytest.raises(QualityError):money_income_intervals(source(),"159001.SZ",basis_shares=basis,par_value=par)


@pytest.mark.parametrize("mutation",["share_units","unit_version","index_end","timezone","overlap"])
def test_normalized_input_is_validated_again_before_consumption(mutation):
    frame=normalized()
    if mutation=="share_units":frame.loc[frame.index[0],"income_per_share"]*=100
    elif mutation=="unit_version":frame.loc[frame.index[0],"par_value"]=1
    elif mutation=="index_end":frame.loc[frame.index[0],"period_end"]=pd.Timestamp("2024-06-18")
    elif mutation=="timezone":frame["period_start"]=frame.period_start.dt.tz_localize("UTC")
    else:frame.loc[frame.index[1],"period_start"]=pd.Timestamp("2024-06-16");frame.loc[frame.index[1],"source_record_type"]="0"
    with pytest.raises(QualityError):validate_money_income_intervals(frame)
