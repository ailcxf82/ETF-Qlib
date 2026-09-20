from __future__ import annotations

import math
from dataclasses import dataclass, field
import numpy as np

from etf_ml.contracts import PortfolioPolicy
from etf_ml.errors import QualityError
from etf_ml.backtest.income import IncomeBook
from etf_ml.data.actions import adjust_mark, recorded_quantity, cash_per_ex_share, adjusted_shares, dividend_entitled_shares


@dataclass
class Account:
    cash: float
    shares: dict[str, float] = field(default_factory=dict)
    marks: dict[str, float] = field(default_factory=dict)
    trades: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    applied_events: set[str] = field(default_factory=set)
    peak_equity: float | None = None
    receivables: dict[str, dict] = field(default_factory=dict)
    registered_holdings: dict[str, dict] = field(default_factory=dict)
    history_start: str | None = None
    history_initially_flat: bool = False
    income_book: IncomeBook = field(default_factory=IncomeBook)

    def equity(self, prices: dict[str, float] | None = None) -> float:
        if prices is not None:
            for instrument, price in prices.items():
                if np.isfinite(price) and price > 0:
                    self.marks[instrument] = float(price)
        missing = set(self.shares) - set(self.marks)
        if missing:
            raise QualityError("Held ETF has no usable valuation history")
        return self.cash + self.receivable_value() + self.income_book.value() + sum(qty * self.marks[instrument]
                               for instrument, qty in self.shares.items())

    def accrue_income(self, day, instrument, income_per_basis):
        held = self.shares.get(instrument, 0.)
        converted = self.income_book.accrue(day, instrument, held, income_per_basis)
        if converted:
            self.shares[instrument] = held + converted
        return converted

    def begin_history(self, day):
        import pandas as pd
        if self.history_start is None:
            self.history_start = str(pd.Timestamp(day).normalize())
            self.history_initially_flat = not any(self.shares.values())

    def record_holdings(self, day):
        import pandas as pd
        self.begin_history(day)
        date = pd.Timestamp(day)
        if (pd.isna(date) or date.tzinfo is not None or date != date.normalize() or
                date < pd.Timestamp(self.history_start) or
                any(not np.isfinite(q) or q < 0 for q in self.shares.values())):
            raise QualityError('Invalid dividend record-day closing holdings')
        key = str(date)
        quantities = {i: float(q) for i, q in self.shares.items() if q > 0}
        if key in self.registered_holdings and self.registered_holdings[key] != quantities:
            raise QualityError('Previously recorded dividend holdings changed')
        self.registered_holdings[key] = quantities

    def dividend_quantity(self, record_date, instrument):
        return recorded_quantity(self.registered_holdings, record_date, self.history_start,
                                 self.history_initially_flat, instrument)

    def receivable_value(self):
        return sum(claim['amount'] for claim in self.receivables.values())

    def settle_receivables(self, day):
        import pandas as pd
        day = pd.Timestamp(day).normalize()
        settled = []
        for identity, claim in list(self.receivables.items()):
            if pd.Timestamp(claim['pay_date']) <= day:
                self.cash += claim['amount']
                settled.append({'event_id': identity, **claim})
                del self.receivables[identity]
        return settled

    def apply_event(self, event_id: str, instrument: str, cash_per_share=0.,
                    share_multiplier=1., *, date=None, pay_date=None, entitled_shares=None,
                    valuation_cash_per_share=None, record_date=None, share_rounding='none') -> None:
        import pandas as pd
        payment = None if pay_date is None or pd.isna(pay_date) else pd.Timestamp(pay_date)
        effective = None if date is None else pd.Timestamp(date)
        if payment is not None and (effective is None or payment.tzinfo is not None or effective.tzinfo is not None or
                payment != payment.normalize() or effective != effective.normalize() or payment < effective):
            raise QualityError('Invalid dividend payment date')
        payment_key = str(payment) if payment is not None else str(date)
        valuation_cash = cash_per_share if valuation_cash_per_share is None else valuation_cash_per_share
        if (not np.isfinite(valuation_cash) or valuation_cash < 0 or
                (entitled_shares is not None and (isinstance(entitled_shares, bool) or
                 not np.isfinite(entitled_shares) or entitled_shares < 0))):
            raise QualityError('Invalid dividend entitlement or valuation units')
        if event_id in self.applied_events:
            previous = next(row for row in self.events if row['event_id'] == event_id)
            if (previous['instrument'], previous['cash_per_share'], previous['share_multiplier'], previous['date'], previous['pay_date'], previous['entitlement_override'], previous['valuation_cash_per_share'], previous['record_date'], previous['share_rounding']) != (instrument, cash_per_share, share_multiplier, str(date), payment_key, entitled_shares, valuation_cash, str(record_date), share_rounding):
                raise QualityError('Previously applied corporate action identity changed')
            return
        if not np.isfinite(cash_per_share) or cash_per_share < 0 or not np.isfinite(share_multiplier) or share_multiplier <= 0:
            raise QualityError("Invalid corporate action")
        quantity = self.shares.get(instrument, 0)
        converted = adjusted_shares(quantity, share_multiplier, share_rounding)
        if share_rounding != "none" and share_multiplier == 1:
            raise QualityError("Share rounding requires a share conversion")
        mark = None
        if quantity:
            if instrument not in self.marks:
                raise QualityError('Held ETF has no pre-action valuation')
            mark = adjust_mark(self.marks[instrument], valuation_cash, share_multiplier)
        registered = quantity if entitled_shares is None else float(entitled_shares)
        amount = registered * cash_per_share
        deferred = payment is not None and effective is not None and payment > effective
        if amount and deferred:
            self.receivables[event_id] = {'instrument': instrument, 'amount': amount,
                                           'ex_date': str(effective), 'pay_date': payment_key}
        else:
            self.cash += amount
        if quantity:
            if converted:
                self.shares[instrument] = converted
            else:
                self.shares.pop(instrument, None)
            self.marks[instrument] = mark
        self.events.append({"event_id": event_id, "instrument": instrument,
                            "date": str(date), "cash": 0. if deferred else amount,
                            "receivable": amount if deferred else 0., "pay_date": payment_key,
                            "cash_per_share": cash_per_share, "share_multiplier": share_multiplier,
                            "entitled_shares": registered, "entitlement_override": entitled_shares,
                            "valuation_cash_per_share": valuation_cash, "record_date": str(record_date),
                            "old_shares": quantity, "new_shares": converted,
                            "share_rounding": share_rounding, "rounding_shares": converted - quantity * share_multiplier})
        self.applied_events.add(event_id)

    def trade(self, instrument: str, intended_shares: float, reference_price: float,
              policy: PortfolioPolicy, *, buyable=True, sellable=True,
              historical_average_amount: float | None = None, date=None) -> dict:
        policy.require_resolved()
        if not np.isfinite(reference_price) or reference_price <= 0:
            raise QualityError("Cannot execute at an invalid raw price")
        if not np.isfinite(intended_shares):
            raise QualityError("Order quantity must be finite")
        direction = "buy" if intended_shares > 0 else "sell"
        reason = None
        if intended_shares == 0:
            reason = "zero_order"
        elif (direction == "buy" and not buyable) or (direction == "sell" and not sellable):
            reason = "not_tradable"
        if historical_average_amount is None or not np.isfinite(historical_average_amount) or historical_average_amount < 0:
            raise QualityError("Orders need known historical liquidity")
        price = reference_price * (1 + policy.slippage_rate if direction == "buy" else
                                   1 - policy.slippage_rate)
        requested = abs(float(intended_shares))
        participation_shares = historical_average_amount * policy.liquidity / price
        quantity = min(requested, participation_shares)
        if direction == "sell":
            quantity = min(quantity, self.shares.get(instrument, 0))
        quantity = math.floor(quantity / policy.lot_size) * policy.lot_size
        if direction == "sell" and self.shares.get(instrument, 0) <= min(requested, participation_shares) + 1e-10:
            # Corporate actions can create odd lots: full liquidation is allowed.
            quantity = self.shares.get(instrument, 0)
        if reason:
            quantity = 0
        if direction == "buy":
            affordable = self.cash / (price * (1 + policy.commission_rate))
            quantity = min(quantity, math.floor(affordable / policy.lot_size) * policy.lot_size)
            while quantity > 0:
                fee = max(quantity * price * policy.commission_rate, policy.minimum_commission)
                if quantity * price + fee <= self.cash + 1e-8:
                    break
                quantity -= policy.lot_size
        amount = quantity * price
        fee = max(amount * policy.commission_rate, policy.minimum_commission) if quantity > 0 else 0
        # Selling a tiny odd lot cannot create negative cash due to a minimum fee.
        settlement = self.income_book.value_for(instrument) if direction == "sell" and quantity > 0 and quantity >= self.shares.get(instrument, 0) - 1e-10 else 0.
        if direction == "sell" and amount + self.cash + settlement < fee:
            quantity, amount, fee, reason = 0, 0, 0, "fee_exceeds_available_cash"
        if quantity:
            sign = 1 if direction == "buy" else -1
            self.cash -= sign * amount + fee
            before_shares = self.shares.get(instrument, 0)
            self.shares[instrument] = self.shares.get(instrument, 0) + sign * quantity
            if abs(self.shares[instrument]) < 1e-10:
                del self.shares[instrument]
            self.marks[instrument] = reference_price
            if direction == "sell":
                self.cash += self.income_book.settle_sale(date, instrument,
                    before_shares=before_shares, after_shares=self.shares.get(instrument, 0), filled_shares=quantity)
        if quantity < requested - 1e-8 and reason is None:
            reason = "liquidity_cash_or_lot_limit"
        result = {"date": str(date), "instrument": instrument, "direction": direction,
                  "requested_shares": requested, "filled_shares": quantity,
                  "reference_price": reference_price, "price": price, "amount": amount,
                  "commission": fee, "slippage_cost": quantity * abs(price - reference_price),
                  "reason": reason, "status": "filled" if quantity >= requested - 1e-8 else
                  ("partial" if quantity else "unfilled")}
        self.trades.append(result)
        if self.cash < -1e-8:
            raise QualityError("Cash conservation failed")
        return result


def reconcile_daily_positions(positions, trades, events, panel, policy, returns, *, income=None, income_rules=None):
    """Replay each booked action/fill, then check cash, shares, value and P&L.

    Filled currency/fees are independently checked against quantities and the
    frozen cost policy; no Qlib daily return is used to advance the replay.
    """
    import pandas as pd
    from etf_ml.data.actions import raw_close_as_of, price_histories
    if (not isinstance(returns.index, pd.DatetimeIndex) or returns.empty or returns.index.has_duplicates or
            not returns.index.is_monotonic_increasing or not np.isfinite(returns).all() or
            set(positions) != set(returns.index) or not trades.datetime.isin(returns.index).all()):
        raise QualityError('Daily account replay dates or returns are incomplete')
    histories = price_histories(panel)
    account = Account(policy.initial_cash)
    account.income_book.rules = dict(income_rules or {})
    income_cursor = returns.index[0] - pd.Timedelta(days=1)
    def post_until(until):
        nonlocal income_cursor
        if income is not None:
            subset = income[(income.period_end > income_cursor) & (income.period_end <= until)]
            for entry in subset.itertuples():
                account.accrue_income(entry.period_end, entry.Index[1], entry.income_per_basis)
        income_cursor = until
    account.begin_history(returns.index[0])
    record_days = set(events.loc[events.cash_per_share.gt(0), 'record_date'].dropna()) if events is not None and 'record_date' in events else set()
    previous = policy.initial_cash
    rows = []
    for day in returns.index:
        post_until(day - pd.Timedelta(days=1))
        account.settle_receivables(day)
        today = events[events.datetime.eq(day)] if events is not None and not events.empty else []
        for event in today.itertuples(index=False) if isinstance(today, pd.DataFrame) else []:
            record = getattr(event, 'record_date', None)
            explicit = event.cash_per_share > 0 and record is not None and pd.notna(record)
            registered = account.dividend_quantity(record, event.instrument) if explicit else None
            if explicit:
                registered = dividend_entitled_shares(registered, event, events)
            account.apply_event(event.event_id, event.instrument, event.cash_per_share, event.share_multiplier,
                date=day, pay_date=getattr(event, 'pay_date', None), entitled_shares=registered,
                valuation_cash_per_share=cash_per_ex_share(event, events), record_date=record if explicit else None,
                share_rounding=getattr(event, "share_rounding", "none"))
        daily_trades = trades[trades.datetime.eq(day)]
        for trade in daily_trades.itertuples(index=False):
            quantity, amount, cost = float(trade.filled_shares), float(trade.amount), float(trade.commission)
            if not all(np.isfinite(value) and value >= 0 for value in [quantity, amount, cost]):
                raise QualityError('Invalid fill in daily account replay')
            if quantity == 0:
                if amount != 0 or cost != 0:
                    raise QualityError('Unfilled order consumed cash in daily account replay')
                continue
            direction = int(trade.direction)
            if direction not in [0, 1] or not np.isfinite(trade.price) or not np.isfinite(trade.reference_price):
                raise QualityError('Invalid fill direction or price in daily account replay')
            expected_price = trade.reference_price * (1 + policy.slippage_rate if direction == 1 else 1 - policy.slippage_rate)
            expected_cost = max(policy.minimum_commission, amount * policy.commission_rate)
            if (not np.isclose(trade.price, expected_price, atol=1e-8, rtol=1e-9) or
                    not np.isclose(amount, quantity * trade.price, atol=1e-6, rtol=1e-9) or
                    not np.isclose(cost, expected_cost, atol=1e-6, rtol=1e-9)):
                raise QualityError('Fill notional, commission or slippage disagrees with frozen policy')
            held = account.shares.get(trade.instrument, 0.)
            new = held + (quantity if direction == 1 else -quantity)
            if new < -1e-7:
                raise QualityError('Daily account replay sold more shares than held')
            account.cash += (-amount if direction == 1 else amount) - cost
            if new > 1e-9:
                account.shares[trade.instrument] = new
                account.marks[trade.instrument] = float(trade.price)
            else:
                account.shares.pop(trade.instrument, None)
            if direction == 0:
                account.cash += account.income_book.settle_sale(day, trade.instrument,
                    before_shares=held, after_shares=max(0., new), filled_shares=quantity)
            if account.cash < -1e-6:
                raise QualityError('Daily account replay has negative cash')
        post_until(day)
        position = positions[day]
        marks, stale = {}, 0
        for instrument in account.shares:
            marks[instrument], quote_day = raw_close_as_of(panel, events, instrument, day, histories=histories)
            stale += quote_day < day
        equity = account.equity(marks)
        qlib_equity = position.calculate_value()
        share_residual = max((abs(position.get_stock_amount(instrument) - account.shares.get(instrument, 0.))
            for instrument in set(position.get_stock_list()) | set(account.shares)), default=0.)
        cash_residual = position.get_cash() - account.cash
        reported_receivable = position.receivable_value() if hasattr(position, 'receivable_value') else 0.
        receivable_residual = reported_receivable - account.receivable_value()
        reported_income = position.income_book.value() if hasattr(position, "income_book") else 0.
        income_residual = reported_income - account.income_book.value()
        value_residual = qlib_equity - equity
        independent_return = equity / previous - 1
        return_residual = float(returns.loc[day]) - independent_return
        if (abs(income_residual) > 1e-6 or abs(receivable_residual) > 1e-6 or share_residual > 1e-6 or not np.isclose(position.get_cash(), account.cash, atol=1e-6, rtol=1e-9) or
                not np.isclose(qlib_equity, equity, atol=1e-6, rtol=1e-9) or
                not np.isclose(float(returns.loc[day]), independent_return, atol=1e-10, rtol=1e-8)):
            raise QualityError('Qlib daily cash, shares, valuation or return disagrees with independent ledger')
        rows.append({'datetime': day, 'independent_cash': account.cash, 'independent_receivable': account.receivable_value(),
                     'reported_receivable': reported_receivable, 'receivable_residual': receivable_residual,
                     'independent_income': account.income_book.value(), 'reported_income': reported_income, 'income_residual': income_residual,
                     'independent_equity': equity,
                     'reported_equity': qlib_equity, 'independent_return': independent_return,
                     'cash_residual': cash_residual, 'maximum_share_residual': share_residual,
                     'equity_residual': value_residual, 'return_residual': return_residual,
                     'held_instruments': len(account.shares), 'stale_valuation_instruments': int(stale)})
        if day in record_days:
            account.record_holdings(day)
        previous = equity
    return pd.DataFrame(rows).set_index('datetime')
