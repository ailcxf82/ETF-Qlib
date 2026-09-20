from __future__ import annotations

from datetime import date

import pandas as pd
from pydantic import Field, model_validator

from etf_ml.contracts import StrictSpec
from etf_ml.errors import ConfigurationError


def local_time(value):
    try:
        result = pd.Timestamp(value)
        if pd.isna(result) or result.tzinfo is None:
            raise ValueError('Timezone required')
        return result.tz_convert('Asia/Shanghai')
    except (ValueError, TypeError) as exc:
        raise ConfigurationError('Operational times require an explicit timezone') from exc


class IngestionReceipt(StrictSpec):
    schema_version: int = Field(default=1, ge=1, le=1)
    snapshot_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    snapshot_manifest_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    trading_day: date
    completed_at: str
    complete: bool = Field(strict=True)
    expected_instruments: list[str] = Field(min_length=1)
    missing_instruments: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def valid_receipt(self):
        local_time(self.completed_at)
        for values in (self.expected_instruments, self.missing_instruments):
            if len(values) != len(set(values)) or any(not value.strip() for value in values):
                raise ValueError('Receipt instrument lists must be unique and nonempty')
        return self


class EquityPoint(StrictSpec):
    date: date
    equity: float = Field(gt=0)


class DividendReceivable(StrictSpec):
    event_id: str = Field(min_length=1)
    instrument: str = Field(min_length=1)
    amount: float = Field(gt=0, strict=True)
    ex_date: date
    pay_date: date


class PaperAccount(StrictSpec):
    schema_version: int = Field(default=1, ge=1, le=1)
    trading_day: date
    cash: float = Field(ge=0)
    shares: dict[str, float]
    receivables: list[DividendReceivable] = Field(default_factory=list)
    income_balances: dict[str, float] = Field(default_factory=dict)
    income_rule_ids: dict[str, str] = Field(default_factory=dict)
    income_as_of: date | None = None
    equity_history: list[EquityPoint] = Field(min_length=1)
    note: str = 'Caller-supplied paper account; no actual fills are asserted'

    @model_validator(mode='after')
    def valid_account(self):
        if set(self.income_balances) != set(self.income_rule_ids):
            raise ValueError('Paper income balances need matching rule identities')
        if self.income_balances and self.income_as_of != self.trading_day:
            raise ValueError('Paper income must be current at the account trading day')
        if any(not i.strip() or not v.strip() for i, v in self.income_rule_ids.items()):
            raise ValueError('Paper income rule identity is empty')
        if len({claim.event_id for claim in self.receivables}) != len(self.receivables):
            raise ValueError('Paper dividend receivables require unique event IDs')
        for claim in self.receivables:
            if not claim.event_id.strip() or not claim.instrument.strip() or not claim.ex_date <= self.trading_day < claim.pay_date:
                raise ValueError('Paper dividend receivable must be earned and not yet payable')
        if any(not name.strip() or quantity < 0 for name, quantity in self.shares.items()):
            raise ValueError('Paper account requires finite long holdings')
        dates = [point.date for point in self.equity_history]
        if dates != sorted(set(dates)) or dates[-1] > self.trading_day:
            raise ValueError('Paper equity history must be unique, ordered and not future')
        return self
