"""Editable quote scenarios: every input is the user's, checked, and echoed in the working."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.boq import scenarios

TRADES = {"civil": Decimal("6000000"), "mechanical": Decimal("2000000")}


def run(**case):
    return scenarios(Decimal("10000000"), Decimal("8000000"), TRADES, {"interest_pct_pa": "12", "cases": [{"name": "X", **case}]})


def test_user_values_drive_the_result():
    r = run(rate_change_pct={"civil": "10"}, delay_months="2", monthly_time_cost="100000")
    c = r["cases"][0]
    assert c["cost"] == "8800000"  # 8,000,000 + 600,000 civil + 200,000 delay
    assert c["margin"] == "1200000" and c["margin_pct_of_price"] == "12.00"
    assert r["trade_costs"] == {"civil": "6000000", "mechanical": "2000000"}


def test_payment_cycle_and_retention_use_the_entered_interest_rate():
    c = run(payment_cycle_days="73", retention_pct="5", retention_months="6")["cases"][0]
    # 1,00,00,000 x 12% x 73/365 = 2,40,000 ; 5% x 1,00,00,000 x 12% x 6/12 = 30,000
    assert c["cost"] == "8270000"


def test_rate_fall_and_blank_fields_are_allowed():
    c = run(rate_change_pct={"civil": "-5", "mechanical": ""}, delay_months="", payment_cycle_days=None)["cases"][0]
    assert c["cost"] == "7700000"


def test_trade_without_items_is_explained_not_guessed():
    c = run(rate_change_pct={"electrical": "8"})["cases"][0]
    assert c["cost"] == "8000000" and "no electrical items" in c["notes"][0]


@pytest.mark.parametrize("case,msg", [({"delay_months": "abc"}, "not a number"), ({"retention_pct": "80"}, "must be 0 to 50"),
                                      ({"payment_cycle_days": "-3"}, "must be 0 to 730")])
def test_bad_inputs_get_a_clear_message(case, msg):
    with pytest.raises(ValueError, match=msg):
        run(**case)
