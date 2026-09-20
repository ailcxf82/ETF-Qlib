"""Non-spendable money-fund income, with explicit cent-allocation governance."""
from __future__ import annotations
from dataclasses import dataclass,field
from decimal import Decimal,ROUND_DOWN
import math
import numpy as np
import pandas as pd
from etf_ml.data.income_supplement import IncomeRule, validate_income_inputs
from etf_ml.errors import QualityError


@dataclass
class IncomeBook:
    rules: dict[str,IncomeRule]=field(default_factory=dict)
    balances: dict[str,Decimal]=field(default_factory=dict)
    records: list[dict]=field(default_factory=list)
    postings: dict[tuple,dict]=field(default_factory=dict)
    last_dates: dict[str,pd.Timestamp]=field(default_factory=dict)

    def _balance(self,instrument):
        amount=self.balances.get(instrument,Decimal(0))
        if not isinstance(amount,Decimal) or not amount.is_finite():
            raise QualityError("Invalid unpaid monetary income state")
        return amount

    def value(self):
        return float(sum((self._balance(i) for i in self.balances),Decimal(0)))

    def value_for(self,instrument):
        return float(self._balance(instrument))

    def accrue(self,day,instrument,held,income_per_basis):
        rule=self.rules.get(instrument)
        if not isinstance(rule,IncomeRule):
            raise QualityError("Income instrument has no declared settlement rule")
        day=pd.Timestamp(day)
        if pd.isna(day) or day.tzinfo is not None or day!=day.normalize():
            raise QualityError("Income booking needs a calendar day")
        if isinstance(held,(bool,np.bool_)) or not isinstance(held,(int,float,np.integer,np.floating)) or not np.isfinite(held) or held<0:
            raise QualityError("Income booking requires actual long holdings")
        if isinstance(income_per_basis,(bool,np.bool_)) or not isinstance(income_per_basis,(int,float,np.integer,np.floating)) or not np.isfinite(income_per_basis):
            raise QualityError("Income booking needs finite signed earnings")
        key=(day,instrument);fingerprint=(rule,float(income_per_basis))
        if key in self.postings:
            old=self.postings[key]
            if (old['fingerprint']!=fingerprint or self.last_dates[instrument]!=day
                    or not math.isclose(held,old['after_shares'],abs_tol=1e-9,rel_tol=0)
                    or self.balances.get(instrument,Decimal(0))!=old['after_balance']):
                raise QualityError("Previously posted income inputs or closing holdings changed")
            return 0.
        if instrument in self.last_dates and day<=self.last_dates[instrument]:
            raise QualityError("Monetary income must be booked in chronological order")
        before=self._balance(instrument);par=Decimal(str(rule.par_value))
        effective=Decimal(str(held))+(before/par if rule.compound_unpaid else Decimal(0))
        if effective<0:
            raise QualityError("Unpaid losses exceed the monetary share entitlement")
        gross=effective*Decimal(str(income_per_basis))/Decimal(str(rule.basis_shares))
        earned=gross.quantize(Decimal('.01'),rounding=ROUND_DOWN)
        if rule.rounding=='exact' and earned!=gross:
            raise QualityError("Income needs final cent-allocation evidence; exact mode refuses truncation")
        after=before+earned
        converted=int(after//par) if rule.convert_at_par and after>=par else 0
        after-=Decimal(converted)*par
        self.balances[instrument]=after;self.last_dates[instrument]=day
        self.postings[key]={'fingerprint':fingerprint,'after_shares':float(held)+converted,'after_balance':after}
        self.records.append({'datetime':day,'instrument':instrument,'kind':'accrual','rule_id':rule.rule_id,
                             'held_shares':float(held),'gross_income':float(gross),'booked_income':float(earned),
                             'unallocated_rounding_residual':float(gross-earned),
                             'converted_shares':converted,'conversion_cash':float(Decimal(converted)*par),
                             'unpaid_before':float(before),'unpaid_after':float(after),'cash_settled':0.})
        return float(converted)

    def settle_sale(self,day,instrument,*,before_shares,after_shares,filled_shares):
        if instrument not in self.rules:
            return 0.
        for quantity in (filled_shares,before_shares,after_shares):
            if isinstance(quantity,(bool,np.bool_)) or not isinstance(quantity,(int,float,np.integer,np.floating)) or not np.isfinite(quantity) or quantity<0:
                raise QualityError("Income sale settlement requires finite actual quantities")
        if not math.isclose(before_shares-after_shares,filled_shares,abs_tol=1e-8,rel_tol=1e-12):
            raise QualityError("Income settlement fill differs from the actual holding change")
        day=pd.Timestamp(day)
        if pd.isna(day) or day.tzinfo is not None:
            raise QualityError("Income settlement requires a Shanghai-local trade day")
        day=day.normalize()
        if filled_shares<=0 or before_shares<=0 or after_shares>1e-9:
            return 0.
        if instrument in self.last_dates and day<=self.last_dates[instrument]:
            raise QualityError("Full-sale settlement cannot follow an already posted closing income day")
        amount=self._balance(instrument)
        self.balances[instrument]=Decimal(0)
        self.records.append({'datetime':pd.Timestamp(day).normalize(),'instrument':instrument,'kind':'full_sale_settlement',
                             'rule_id':self.rules[instrument].rule_id,'held_shares':float(before_shares),
                             'gross_income':0.,'booked_income':0.,'unallocated_rounding_residual':0.,
                             'converted_shares':0.,'conversion_cash':0.,'unpaid_before':float(amount),
                             'unpaid_after':0.,'cash_settled':float(amount)})
        return float(amount)
