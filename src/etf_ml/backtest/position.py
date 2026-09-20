"""Qlib raw-share position with non-spendable dated dividend claims."""
from __future__ import annotations

import pandas as pd
from qlib.backtest.position import Position
from qlib.backtest.decision import Order
from etf_ml.backtest.income import IncomeBook


class ETFPosition(Position):
    def __init__(self, *args, **kwargs):
        # Keep claims outside position.position so they never become stock IDs
        # or the framework's bar-settled cash_delay balance.
        self.dividend_receivables = {}
        self.income_book = IncomeBook()
        super().__init__(*args, **kwargs)

    def receivable_value(self):
        return sum(claim['amount'] for claim in self.dividend_receivables.values())

    def calculate_value(self):
        return super().calculate_value() + self.receivable_value() + self.income_book.value()

    def accrue_income(self, day, instrument, income_per_basis):
        held = self.get_stock_amount(instrument)
        converted = self.income_book.accrue(day, instrument, held, income_per_basis)
        if converted:
            self.position[instrument]["amount"] = held + converted
        return converted

    def update_order(self, order, trade_val, cost, trade_price):
        before = self.get_stock_amount(order.stock_id)
        super().update_order(order, trade_val, cost, trade_price)
        if order.direction == Order.SELL and trade_val > 0:
            self.position["cash"] += self.income_book.settle_sale(order.start_time, order.stock_id,
                before_shares=before, after_shares=self.get_stock_amount(order.stock_id),
                filled_shares=trade_val / trade_price)

    def settle_dividends(self, day):
        for identity, claim in list(self.dividend_receivables.items()):
            if pd.Timestamp(claim['pay_date']) <= day:
                self.position['cash'] += claim['amount']
                del self.dividend_receivables[identity]
