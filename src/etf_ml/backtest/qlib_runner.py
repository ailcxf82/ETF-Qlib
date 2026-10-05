from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from qlib.backtest.exchange import Exchange
from qlib.backtest.executor import SimulatorExecutor
from etf_ml.backtest.income import validate_income_inputs
from qlib.backtest.decision import Order, TradeDecisionWO
from qlib.strategy.base import BaseStrategy

from etf_ml.backtest.accounting import Account, reconcile_daily_positions
from etf_ml.backtest.execution import TRADE_COLUMNS, execution_metrics
from etf_ml.backtest.metrics import portfolio_metrics, position_exposures
from etf_ml.contracts import PortfolioPolicy, ValidationSpec
from etf_ml.data.calendar import rebalance_dates, require_calendar
from etf_ml.data.actions import validate_events, adjust_mark, raw_close_as_of, price_histories, recorded_quantity, cash_per_ex_share, adjusted_shares, dividend_entitled_shares
from etf_ml.data.source import require_panel
from etf_ml.data.normalize import trading_permissions, trading_reasons, row_trading_permissions
from etf_ml.errors import QualityError
from etf_ml.portfolio.allocation import construct


class ETFExchange(Exchange):
    """Raw-share execution plus an independent replay of Qlib cash movements."""

    def __init__(self, panel, policy, *, start_time, end_time, events=None, income=None, income_rules=None):
        self.panel, self.policy = panel, policy
        self.price_histories = price_histories(panel)
        self.amount_histories = {instrument: part.droplevel("instrument").amount_currency
                                 for instrument, part in panel[["amount_currency"]].groupby(level="instrument", sort=False)}
        self.events = events
        self.income = income
        self.income_rules = dict(income_rules or {})
        self.income_cursor = pd.Timestamp(start_time).normalize() - pd.Timedelta(days=1)
        self.replay = Account(policy.initial_cash)
        self.replay.income_book.rules = dict(self.income_rules)
        self.audit = []
        self.daily_dealt_notional = {}
        self.daily_dealt_shares = {}
        super().__init__(codes=list(panel.index.get_level_values("instrument").unique()),
                         start_time=start_time, end_time=end_time, freq="day",
                         deal_price="$open", limit_threshold=None,
                         open_cost=policy.commission_rate, close_cost=policy.commission_rate,
                         min_cost=policy.minimum_commission, impact_cost=0,
                         trade_unit=policy.lot_size)

    def get_quote_from_qlib(self):
        """Feed Qlib its quote interface from the verified raw execution panel.

        This adapter overrides prices, lots and ADV in raw units. Reloading
        thousands of adjusted provider files per backtest is redundant and
        would expose different units to Qlib's execution indicators.
        """
        dates = self.panel.index.get_level_values("datetime")
        frame = self.panel[(dates >= pd.Timestamp(self.start_time).normalize()) &
                           (dates <= pd.Timestamp(self.end_time).normalize())]
        fields = {"$open": "raw_open", "$close": "raw_close", "$change": "return_1d",
                  "$volume": "volume_shares"}
        if not set(self.all_fields).issubset(set(fields) | {"$factor"}):
            raise QualityError("ETF quote requests an unsupported execution field")
        self.quote_df = pd.DataFrame({name: (1. if name == "$factor" else frame[column])
                                     for name, column in {**fields, "$factor": None}.items()}, index=frame.index)
        self.quote_df = self.quote_df.reorder_levels(["instrument", "datetime"]).sort_index()
        self.trade_w_adj_price = False
        # Directional permissions and sourced daily bounds are authoritative.
        self._update_limit(None)

    def apply_income_until(self, position, until):
        if self.income is None:
            return
        position.income_book.rules = dict(self.income_rules)
        until = pd.Timestamp(until).normalize()
        if until < self.income_cursor:
            raise QualityError("Income execution cursor cannot move backwards")
        rows = self.income[(self.income.period_end > self.income_cursor) & (self.income.period_end <= until)]
        for entry in rows.itertuples():
            instrument = entry.Index[1]
            position.accrue_income(entry.period_end, instrument, entry.income_per_basis)
            self.replay.accrue_income(entry.period_end, instrument, entry.income_per_basis)
        if not np.isclose(position.income_book.value(), self.replay.income_book.value(), atol=1e-6, rtol=1e-9):
            raise QualityError("Qlib income balance disagrees with independent execution replay")
        self.income_cursor = until

    def row(self, stock_id, time):
        key = (pd.Timestamp(time).normalize(), stock_id)
        return self.panel.loc[key] if key in self.panel.index else None

    def is_stock_tradable(self, stock_id, start_time, end_time, direction=None):
        row = self.row(stock_id, start_time)
        if row is None or not row.tradable or not np.isfinite(row.raw_open) or row.raw_open <= 0:
            return False
        field = "buyable" if direction == Order.BUY else "sellable"
        if not row_trading_permissions(row)[field]:
            return False
        if "up_limit" in row or "down_limit" in row:
            lower, upper = row.get("down_limit", np.nan), row.get("up_limit", np.nan)
            price = self.get_deal_price(stock_id, start_time, end_time, direction)
            if not (np.isfinite(lower) and np.isfinite(upper) and lower <= price <= upper):
                return False
        return True

    def get_factor(self, stock_id, start_time, end_time, method="last"):
        # This ETF adapter uses raw prices and shares; actions are booked explicitly.
        return 1.0

    def get_close(self, stock_id, start_time, end_time, method="last"):
        row = self.row(stock_id, start_time)
        if row is not None and np.isfinite(row.raw_close) and row.raw_close > 0:
            return float(row.raw_close)
        price, _ = raw_close_as_of(self.panel, self.events, stock_id, end_time, histories=self.price_histories)
        return price

    def get_deal_price(self, stock_id, start_time, end_time, direction, method="last"):
        row = self.row(stock_id, start_time)
        if row is None or not np.isfinite(row.raw_open) or row.raw_open <= 0:
            return np.nan
        return float(row.raw_open * (1 + self.policy.slippage_rate if direction == Order.BUY
                                     else 1 - self.policy.slippage_rate))

    def historical_amount(self, stock_id, time):
        date = pd.Timestamp(time).normalize()
        history = self.amount_histories.get(stock_id)
        if history is None:
            return 0.
        location = history.index.searchsorted(date, side="left")
        tail = history.iloc[max(0, location - self.policy.liquidity_lookback):location]
        if len(tail) < self.policy.liquidity_lookback or tail.isna().any():
            return 0.
        return float(tail.mean())

    def _remaining_liquidity(self, order, dealt_order_amount):
        key = (pd.Timestamp(order.start_time).normalize(), order.stock_id)
        supplied = float(dealt_order_amount.get(order.stock_id, 0.))
        known_shares = self.daily_dealt_shares.get(key, 0.)
        if (not np.isfinite(supplied) or supplied < 0 or
                supplied > known_shares + 1e-8):
            raise QualityError("Prior daily fills need an exchange currency execution history")
        capacity = self.historical_amount(order.stock_id, order.start_time) * self.policy.liquidity
        return max(0., capacity - self.daily_dealt_notional.get(key, 0.))

    def _clip_amount_by_volume(self, order, dealt_order_amount):
        price = self.get_deal_price(order.stock_id, order.start_time, order.end_time, order.direction)
        remaining = self._remaining_liquidity(order, dealt_order_amount) / price
        order.deal_amount = min(order.deal_amount, remaining)

    def round_amount_by_trade_unit(self, deal_amount, factor=None, stock_id=None,
                                   start_time=None, end_time=None):
        if not np.isfinite(deal_amount) or deal_amount < 0:
            raise QualityError("ETF lot rounding needs a finite nonnegative quantity")
        # Raw shares: do not use Qlib's +0.1-share tolerance, which can overfill
        # cash/participation limits just below a lot boundary.
        return math.floor(float(deal_amount) / self.policy.lot_size) * self.policy.lot_size

    def get_factor(self, stock_id, start_time, end_time):
        # Qlib factors are for adjusted-share accounting. This runner books
        # raw ETF shares, so applying them would create synthetic fractions.
        return 1.

    def _get_buy_amount_by_cash_limit(self, trade_price, cash, cost_ratio):
        # Both inequalities must hold, including a zero proportional commission.
        notional = min(cash / (1 + cost_ratio), cash - self.min_cost)
        return max(0., notional) / trade_price

    def _calc_trade_info_by_order(self, order, position, dealt_order_amount):
        if order.direction != Order.SELL:
            return super()._calc_trade_info_by_order(order, position, dealt_order_amount)
        if position is None:
            raise QualityError("ETF selling requires the actual held position")
        price = self.get_deal_price(
            order.stock_id, order.start_time, order.end_time, order.direction)
        order.factor = 1.
        order.deal_amount = float(order.amount)
        self._clip_amount_by_volume(order, dealt_order_amount)
        held = float(position.get_stock_amount(order.stock_id))
        if not np.isfinite(held) or held < 0:
            raise QualityError("ETF selling requires finite long holdings")
        clipped = min(held, order.deal_amount)
        if held > 0 and clipped >= held - 1e-10:
            # A full liquidation preserves corporate-action odd/fractional shares.
            order.deal_amount = held
        else:
            order.deal_amount = self.round_amount_by_trade_unit(clipped, 1.)
        value = order.deal_amount * price
        fee = max(value * self.close_cost, self.min_cost) if order.deal_amount else 0.
        income_book = getattr(position, "income_book", None)
        income_settlement = (income_book.value_for(order.stock_id)
            if income_book is not None and order.deal_amount > 0 and order.deal_amount >= held - 1e-10 else 0.)
        if position.get_cash() + value + income_settlement < fee:
            order.deal_amount, value, fee = 0., 0., 0.
        return price, value, fee

    def deal_order(self, order, trade_account=None, position=None, dealt_order_amount=None):
        if (not np.isfinite(order.amount) or order.amount <= 0 or
                order.direction not in (Order.BUY, Order.SELL)):
            raise QualityError("ETF order needs a positive finite quantity and valid direction")
        dealt_order_amount = {} if dealt_order_amount is None else dealt_order_amount
        # This exchange trades raw shares against raw prices. Qlib's default
        # factor conversion would otherwise turn a filled raw quantity into an
        # adjusted quantity when it updates the position.
        order.factor = 1.
        requested = order.amount
        raw = self.row(order.stock_id, order.start_time)
        amount, cost, price = super().deal_order(
            order, trade_account=trade_account, position=position,
            dealt_order_amount=dealt_order_amount)
        target = trade_account.current_position if trade_account else position
        historical = self.historical_amount(order.stock_id, order.start_time)
        replay = None
        if raw is not None and np.isfinite(raw.raw_open) and raw.raw_open > 0:
            # Both directions consume actual traded currency, irrespective of slip direction.
            available_amount = self._remaining_liquidity(order, dealt_order_amount)
            replay = self.replay.trade(
                order.stock_id, requested if order.direction == Order.BUY else -requested,
                float(raw.raw_open), self.policy,
                buyable=self.is_stock_tradable(order.stock_id, order.start_time, order.end_time, Order.BUY),
                sellable=self.is_stock_tradable(order.stock_id, order.start_time, order.end_time, Order.SELL),
                historical_average_amount=available_amount / self.policy.liquidity,
                date=order.start_time)
            if not np.isclose(replay["amount"], amount, atol=1e-6, rtol=1e-9) or not np.isclose(replay["commission"], cost, atol=1e-6):
                raise QualityError("Qlib trade disagrees with independent ETF ledger")
        if target is not None:
            if not np.isclose(target.get_cash(), self.replay.cash, atol=1e-6, rtol=1e-9):
                raise QualityError(
                    "Qlib cash disagrees with independent ETF ledger: "
                    f"date={pd.Timestamp(order.start_time).normalize().date()} "
                    f"instrument={order.stock_id} qlib_cash={target.get_cash():.10f} "
                    f"ledger_cash={self.replay.cash:.10f}"
                )
            for instrument in set(target.get_stock_list()) | set(self.replay.shares):
                if not np.isclose(target.get_stock_amount(instrument),
                                  self.replay.shares.get(instrument, 0), atol=1e-6):
                    raise QualityError("Qlib shares disagree with independent ETF ledger")
        key = (pd.Timestamp(order.start_time).normalize(), order.stock_id)
        self.daily_dealt_notional[key] = self.daily_dealt_notional.get(key, 0.) + amount
        self.daily_dealt_shares[key] = self.daily_dealt_shares.get(key, 0.) + order.deal_amount
        reference = float(raw.raw_open) if raw is not None and np.isfinite(raw.raw_open) and raw.raw_open > 0 else None
        filled = float(order.deal_amount)
        status = "unfilled" if filled == 0 else (
            "filled" if np.isclose(filled, requested, atol=1e-8, rtol=0) else "partial")
        reason = replay["reason"] if replay is not None else "no_usable_reference_quote"
        if reason == "not_tradable" and raw is not None:
            side = "buy_reason" if order.direction == Order.BUY else "sell_reason"
            source_reason = trading_reasons(self.panel.loc[[raw.name]]).iloc[0][side]
            if source_reason is not None:
                reason = source_reason
            elif "up_limit" in raw or "down_limit" in raw:
                reason = "execution_price_outside_limits"
        self.audit.append({"datetime": pd.Timestamp(order.start_time).normalize(),
                           "instrument": order.stock_id, "direction": int(order.direction),
                           "requested_shares": requested, "filled_shares": filled,
                           "amount": amount, "commission": cost,
                           "price": price if np.isfinite(price) else None,
                           "reference_price": reference,
                           "slippage_cost": filled * abs(float(price) - reference) if filled else 0.,
                           "status": status, "reason": reason,
                           "historical_average_amount": historical})
        return amount, cost, price


class ETFIncomeExecutor(SimulatorExecutor):
    """Book closing income after fills, before Qlib metrics/positions sampling."""
    def _collect_data(self, trade_decision, level=0):
        result = super()._collect_data(trade_decision, level=level)
        day = self.trade_calendar.get_step_time()[0].normalize()
        self.trade_exchange.apply_income_until(self.trade_account.current_position, day)
        return result


class ETFRotationStrategy(BaseStrategy):
    def __init__(self, predictions, panel, universe, calendar, policy, events=None,
                 annualization_days=252, **kwargs):
        self.annualization_days = annualization_days
        self.predictions, self.panel, self.universe = predictions, panel, universe
        self.calendar, self.policy = calendar, policy
        self.execution_dates = set(rebalance_dates(calendar, policy.mid_month_day))
        self.events = events if events is not None else pd.DataFrame()
        self.applied_events = set()
        self.registered_holdings = {}
        self.history_start = None
        self.history_initially_flat = False
        self.entitlements = []
        self.record_days = set(self.events.loc[self.events.cash_per_share.gt(0), 'record_date'].dropna()) if 'record_date' in self.events else set()
        self.decisions = []
        self.risk_checks = []
        self.peak = policy.initial_cash
        self.initial_equity = policy.initial_cash
        self.previous_value = policy.initial_cash
        self.daily_returns = []
        super().__init__(**kwargs)

    def _actions(self, date):
        position = self.trade_position
        if self.history_start is None:
            self.history_start = date
            self.history_initially_flat = not position.get_stock_list()
            self.trade_exchange.replay.begin_history(date)
        location = self.calendar.get_indexer([date])[0]
        # Qlib may apply provider adjustment factors between bars. The ETF
        # engine keeps raw shares, so restore the independently reconciled raw
        # quantities before recording entitlements or applying actions.
        for instrument in list(position.get_stock_list()):
            expected = self.trade_exchange.replay.shares.get(instrument, 0.)
            if expected > 1e-10:
                position.position[instrument]["amount"] = expected
            else:
                del position.position[instrument]
        if location > 0 and self.calendar[location-1] >= self.history_start:
            previous = self.calendar[location-1]
            if previous in self.record_days:
                self.registered_holdings[str(previous)] = {i: position.get_stock_amount(i) for i in position.get_stock_list()}
                self.trade_exchange.replay.record_holdings(previous)
        self.trade_exchange.apply_income_until(position, date - pd.Timedelta(days=1))
        position.settle_dividends(date)
        self.trade_exchange.replay.settle_receivables(date)
        if self.events.empty:
            return
        today = self.events[pd.to_datetime(self.events.datetime).dt.normalize() == date]
        position = self.trade_position
        for idx, row in today.iterrows():
            event_id = str(row.get("event_id", f"{date}-{row.instrument}-{idx}"))
            if event_id in self.applied_events:
                continue
            cash = float(row.get("cash_per_share", 0))
            multiplier = float(row.get("share_multiplier", 1))
            quantity = position.get_stock_amount(row.instrument)
            record = row.get('record_date')
            explicit = cash > 0 and record is not None and pd.notna(record)
            registered = recorded_quantity(self.registered_holdings, record, self.history_start,
                self.history_initially_flat, row.instrument) if explicit else quantity
            independent_registered = self.trade_exchange.replay.dividend_quantity(record, row.instrument) if explicit else None
            if explicit:
                registered = dividend_entitled_shares(registered, row, self.events)
                independent_registered = dividend_entitled_shares(independent_registered, row, self.events)
            valuation_cash = cash_per_ex_share(row, self.events)
            if quantity:
                self.trade_exchange.replay.marks[row.instrument] = position.get_stock_price(row.instrument)
            self.trade_exchange.replay.apply_event(
                event_id, row.instrument, cash, multiplier, date=date, pay_date=row.get('pay_date'),
                entitled_shares=independent_registered, valuation_cash_per_share=valuation_cash, record_date=record if explicit else None,
                share_rounding=row.get("share_rounding", "none"))
            payment = row.get('pay_date')
            if payment is not None and pd.notna(payment) and payment > date and cash and registered:
                position.dividend_receivables[event_id] = {'instrument': row.instrument,
                    'amount': registered * cash, 'ex_date': str(date), 'pay_date': str(payment)}
            else:
                position.position["cash"] += registered * cash
            if quantity:
                converted = adjusted_shares(quantity, multiplier, row.get("share_rounding", "none"))
                if converted:
                    position.position[row.instrument]["amount"] = converted
                    position.position[row.instrument]["price"] = adjust_mark(position.position[row.instrument]["price"], valuation_cash, multiplier)
                else:
                    del position.position[row.instrument]
            if cash:
                self.entitlements.append({'event_id': event_id, 'instrument': row.instrument,
                    'ex_date': str(date), 'record_date': str(record) if explicit else None,
                    'entitled_shares': registered, 'ex_date_shares': quantity,
                    'cash_per_record_share': cash, 'cash_per_ex_share': valuation_cash,
                    'distribution_amount': registered * cash,
                    'basis': ('post_record_conversions' if row.get('cash_share_basis') == 'post_record_conversions' else 'record_day_close') if explicit else 'pre_action_compatibility'})
            self.applied_events.add(event_id)

    def _record_unchecked_risk(self, date, reason, signal_date=None):
        row = {"date": str(date.date()), "checked": False, "reason": reason,
               "risk_mode": self.policy.risk_mode,
               "decision_valuation_basis": None, "execution_reference_price_basis": None,
               "decision_execution_timing_status": "not_checked",
               "drawdown_limit": float(self.policy.max_drawdown_limit) if self.policy.risk_mode == "max_drawdown" else None,
               "risk_trigger_limit": float(self.policy.risk) if self.policy.risk_mode == "max_drawdown" else None,
               "risk_order_intents": [], "risk_order_blockers": []}
        if signal_date is not None:
            row["signal_date"] = str(signal_date.date())
        self.risk_checks.append(row)

    def generate_trade_decision(self, execute_result=None):
        step = self.trade_calendar.get_trade_step()
        start, end = self.trade_calendar.get_step_time(step)
        date = pd.Timestamp(start).normalize()
        self._actions(date)
        location = self.calendar.get_indexer([date])[0]
        if location <= 0:
            self._record_unchecked_risk(date, "no_prior_session")
            return TradeDecisionWO([], self)
        signal_date = self.calendar[location - 1]
        # post_exe_step observes the completed bar, including costs and income.
        # Never use this session's open to decide a same-open risk liquidation.
        drawdown = 1 - self.previous_value / self.peak
        volatility = (float(np.std(self.daily_returns[-20:], ddof=1) * np.sqrt(self.annualization_days))
                      if len(self.daily_returns) >= 20 else None)
        risk_triggered = (drawdown >= self.policy.risk if self.policy.risk_mode == "max_drawdown"
                          else volatility is not None and volatility > self.policy.risk)
        risk_check = {"date": str(date.date()), "signal_date": str(signal_date.date()),
                      "checked": True, "reason": None, "risk_mode": self.policy.risk_mode,
                      "planned_rebalance": date in self.execution_dates,
                      "decision_valuation_basis": "previous_session_close",
                      "execution_reference_price_basis": "current_session_open",
                      "decision_execution_timing_status": "prior_close_risk_next_open_execution",
                      "order_sizing_basis": None,
                      "equity_at_decision": float(self.previous_value),
                      "initial_equity": float(self.initial_equity),
                      "peak_equity_at_decision": float(self.peak), "drawdown": float(drawdown),
                      "drawdown_limit": float(self.policy.max_drawdown_limit) if self.policy.risk_mode == "max_drawdown" else None,
                      "risk_trigger_limit": float(self.policy.risk) if self.policy.risk_mode == "max_drawdown" else None,
                      "annualized_volatility_20d": volatility,
                      "volatility_limit": float(self.policy.risk) if self.policy.risk_mode == "annualized_volatility" else None,
                      "risk_triggered": bool(risk_triggered),
                      "trigger_limit_exceeded": bool(risk_triggered),
                      "acceptance_limit_exceeded": (bool(drawdown > self.policy.max_drawdown_limit)
                          if self.policy.risk_mode == "max_drawdown" else None),
                      "risk_limit_exceeded": (bool(drawdown > self.policy.max_drawdown_limit)
                          if self.policy.risk_mode == "max_drawdown" else
                          bool(risk_triggered) if volatility is not None else None),
                      "generated_order_count": 0, "risk_order_intents": [], "risk_order_blockers": []}
        self.risk_checks.append(risk_check)
        if risk_triggered:
            # Shares, not current-open target weights, define risk orders. Submit
            # blocked orders too so exchange receipts retain the exact reason.
            position = self.trade_position
            scale = 0. if self.policy.risk_mode == "max_drawdown" else self.policy.risk / volatility
            orders = []
            for instrument in sorted(position.get_stock_list()):
                held = position.get_stock_amount(instrument)
                target = math.floor(held * scale / self.policy.lot_size) * self.policy.lot_size
                if held > target:
                    orders.append(Order(stock_id=instrument, amount=held - target,
                                        start_time=start, end_time=end, direction=Order.SELL))
            risk_check["order_sizing_basis"] = "held_shares_and_prior_close_risk"
            risk_check["generated_order_count"] = len(orders)
            risk_check["risk_order_intents"] = [
                {"instrument": order.stock_id, "direction": "sell", "requested_shares": float(order.amount),
                 "order_date": str(date.date()), "signal_date": str(signal_date.date())} for order in orders]
            self.decisions.append({"date": str(date.date()), "signal_date": str(signal_date.date()),
                                   "decision_kind": "risk_reduction", "risk_triggered": True,
                                   "reasons": {"_risk": "risk_limit_triggered"}})
            return TradeDecisionWO(orders, self)
        if date not in self.execution_dates:
            return TradeDecisionWO([], self)
        if signal_date not in self.predictions.index.get_level_values("datetime"):
            self.decisions.append({"date": str(date.date()), "reason": "missing_previous_signal"})
            risk_check["rebalance_skip_reason"] = "missing_previous_signal"
            return TradeDecisionWO([], self)
        scores = self.predictions.xs(signal_date, level="datetime")
        eligibility = self.universe.xs(signal_date, level="datetime")
        scores = scores.reindex(eligibility.index[eligibility.eligible]).dropna()
        position = self.trade_position
        # Execution-time prices size orders; decision features and liquidity remain historical.
        for instrument in position.get_stock_list():
            row = self.trade_exchange.row(instrument, date)
            if row is not None and np.isfinite(row.raw_open) and row.raw_open > 0:
                position.update_stock_price(instrument, float(row.raw_open))
        equity = position.calculate_value()
        risk_check["order_sizing_basis"] = "current_session_open_target_weight_sizing_unverified"
        candidates = set(scores.index) | set(position.get_stock_list())
        buyable = {i: self.trade_exchange.is_stock_tradable(i, start, end, Order.BUY) for i in candidates}
        sellable = {i: self.trade_exchange.is_stock_tradable(i, start, end, Order.SELL) for i in candidates}
        groups = eligibility.tracking_group.to_dict()
        for instrument in position.get_stock_list():
            if instrument not in groups:
                history = self.universe.xs(instrument, level="instrument")
                past = history[history.index < date].tracking_group.dropna()
                groups[instrument] = past.iloc[-1] if len(past) else instrument
        allocation = construct(scores, position.get_stock_weight_dict(), {
            "buyable": buyable, "sellable": sellable, "tracking_group": groups,
            "risk_triggered": risk_triggered, "receivable_weight": position.receivable_value() / equity,
            "income_weight": position.income_book.value() / equity}, self.policy)
        weights = dict(allocation.weights)
        if self.policy.risk_mode == "annualized_volatility":
            if len(self.daily_returns) < 20:
                weights = {i: w for i, w in weights.items() if i in position.get_stock_list() and not sellable[i]}
            else:
                volatility = np.std(self.daily_returns[-20:], ddof=1) * np.sqrt(self.annualization_days)
                scale = min(1., self.policy.risk / volatility) if volatility > 0 else 1.
                weights = {i: w if not sellable.get(i, False) else w * scale for i, w in weights.items()}
        orders = []
        for instrument in sorted(set(weights) | set(position.get_stock_list())):
            row = self.trade_exchange.row(instrument, date)
            if row is None or not np.isfinite(row.raw_open) or row.raw_open <= 0:
                continue
            current = position.get_stock_amount(instrument)
            target = math.floor(weights.get(instrument, 0) * equity /
                                row.raw_open / self.policy.lot_size) * self.policy.lot_size
            delta = target - current
            if abs(delta) > 1e-8:
                orders.append(Order(stock_id=instrument, amount=abs(delta), start_time=start, end_time=end, direction=
                                    Order.BUY if delta > 0 else Order.SELL))
        orders.sort(key=lambda order: (order.direction == Order.BUY, order.stock_id))
        self.decisions.append({"date": str(date.date()), "signal_date": str(signal_date.date()),
                               "weights": weights, "cash_weight": max(0, 1 - position.receivable_value() / equity - position.income_book.value() / equity - sum(weights.values())),
                               "receivable_weight": position.receivable_value() / equity,
                               "income_weight": position.income_book.value() / equity,
                               "risk_triggered": bool(risk_triggered),
                               "reasons": allocation.reasons})
        risk_check["generated_order_count"] = len(orders)
        risk_check["allocation_reasons"] = list(allocation.reasons)
        return TradeDecisionWO(orders, self)

    def post_exe_step(self, execute_result=None):
        value = self.trade_position.calculate_value()
        self.daily_returns.append(value / self.previous_value - 1)
        self.previous_value = value
        self.peak = max(self.peak, value)


@dataclass
class BacktestResult:
    report: pd.DataFrame
    trades: pd.DataFrame
    positions: dict
    daily_returns: pd.Series
    metrics: dict
    decisions: list[dict]
    exposures: pd.DataFrame = field(default_factory=pd.DataFrame)
    execution: pd.DataFrame = field(default_factory=pd.DataFrame)
    ledger: pd.DataFrame = field(default_factory=pd.DataFrame)
    entitlements: pd.DataFrame = field(default_factory=pd.DataFrame)
    income_postings: pd.DataFrame = field(default_factory=pd.DataFrame)
    risk_checks: list[dict] = field(default_factory=list)


def evaluate(predictions: pd.Series, policy: PortfolioPolicy, snapshot, *,
             universe: pd.DataFrame, calendar: pd.DatetimeIndex, benchmark: pd.DataFrame,
             provider: Path, recorder_uri: Path, start_time=None, end_time=None,
             events=None, schedule_calendar=None, annualization_days=252,
             risk_free_rate=0., income=None, income_rules=None,
             allow_income_rounding_proxy=False) -> BacktestResult:
    import qlib
    from qlib.backtest import backtest
    from qlib.constant import REG_CN
    policy.require_resolved()
    assumptions = ValidationSpec(annualization_days=annualization_days,
                                 risk_free_rate=risk_free_rate)
    panel = snapshot
    require_panel(panel)
    require_panel(predictions, numeric=True)
    require_calendar(calendar)
    if benchmark.attrs.get("benchmark_id") != "CSI300":
        raise QualityError("Primary benchmark must be CSI300")
    start_time = pd.Timestamp(start_time or predictions.index.get_level_values("datetime").min())
    end_time = pd.Timestamp(end_time or predictions.index.get_level_values("datetime").max())
    previous_close = benchmark.close.shift(1)
    benchmark_returns = (benchmark.close / previous_close - 1).loc[start_time:end_time]
    if benchmark_returns.isna().any():
        raise QualityError("Benchmark needs a prior close and complete evaluation range")
    if isinstance(events, pd.DataFrame):
        # Source evidence remains hash-bound in the immutable snapshot. Pandas
        # deep-copies attrs on column selection; copying thousands of proof
        # hashes on every execution/valuation query is unnecessary.
        events = events.copy(deep=False)
        events.attrs = {}
    events, events_validation = validate_events(events, calendar, panel.index.get_level_values('instrument').unique())
    income, income_rules, income_validation = validate_income_inputs(
        income, income_rules, panel.index.get_level_values('instrument').unique(),
        calendar, start_time, end_time, allow_rounding_proxy=allow_income_rounding_proxy)
    if income is not None and not events.empty and events.instrument.isin(income_rules).any():
        raise QualityError("Monetary income and cash/share corporate actions require separate unit-version handling")
    qlib.init(provider_uri=provider, region=REG_CN, kernels=1,
              exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                           "kwargs": {"uri": Path(recorder_uri).resolve().as_uri(),
                                      "default_exp_name": "etf-backtest"}})
    exchange = ETFExchange(panel, policy, start_time=start_time, end_time=end_time, events=events, income=income, income_rules=income_rules)
    execution_calendar = schedule_calendar if schedule_calendar is not None else calendar
    strategy = ETFRotationStrategy(predictions, panel, universe, execution_calendar, policy, events,
                                   annualization_days=assumptions.annualization_days)
    portfolios, _ = backtest(start_time=start_time, end_time=end_time, strategy=strategy,
                             executor={"class": "ETFIncomeExecutor",
                                       "module_path": "etf_ml.backtest.qlib_runner",
                                       "kwargs": {"time_per_step": "day", "generate_portfolio_metrics": True}},
                             benchmark=benchmark_returns, account=policy.initial_cash,
                             pos_type="etf_ml.backtest.position.ETFPosition",
                             exchange_kwargs={"exchange": exchange})
    report, positions = portfolios["1day"]
    returns = (report["return"] - report["cost"]).rename("net_return")
    metrics = portfolio_metrics(returns, benchmark_returns.reindex(returns.index),
                                annualization_days=assumptions.annualization_days,
                                risk_free_rate=assumptions.risk_free_rate)
    exposure_metrics, exposures = position_exposures(positions, universe)
    if not exposures.index.equals(returns.index):
        raise QualityError("Exposure and portfolio return dates differ")
    metrics.update(exposure_metrics)
    trades = pd.DataFrame(exchange.audit, columns=TRADE_COLUMNS)
    execution_summary, execution = execution_metrics(trades, returns.index, policy.initial_cash)
    metrics.update(execution_summary)
    metrics["turnover"] = float(report["turnover"].sum())
    metrics["turnover_definition"] = "sum_daily_traded_value_over_equity"
    ledger = reconcile_daily_positions(positions, trades, events, panel, policy, returns, income=income, income_rules=income_rules)
    metrics['events_validation'] = events_validation
    metrics['income_validation'] = income_validation
    metrics['max_income_residual'] = float(ledger.income_residual.abs().max())
    metrics['accounting_reconciled'] = True
    metrics['daily_accounting_reconciled'] = True
    metrics['daily_accounting_dates'] = len(ledger)
    metrics['max_receivable_residual'] = float(ledger.receivable_residual.abs().max())
    metrics['max_cash_residual'] = float(ledger.cash_residual.abs().max())
    metrics['max_equity_residual'] = float(ledger.equity_residual.abs().max())
    metrics['max_return_residual'] = float(ledger.return_residual.abs().max())
    metrics['stale_valuation_dates'] = int(ledger.stale_valuation_instruments.gt(0).sum())
    return BacktestResult(report, trades, positions, returns, metrics, strategy.decisions, exposures, execution, ledger,
                          pd.DataFrame(strategy.entitlements), pd.DataFrame(exchange.replay.income_book.records),
                          strategy.risk_checks)
