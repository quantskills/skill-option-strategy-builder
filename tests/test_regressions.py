from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import legs  # noqa: E402
import payoff  # noqa: E402
import pricing  # noqa: E402
import strategy_card  # noqa: E402


def contract(symbol, option_type, strike, expiry):
    return {
        "symbol": symbol,
        "call_put_code": "CO" if option_type == "call" else "PO",
        "strike_price": strike,
        "delisted_date": expiry,
        "contract_size": 10_000,
        "margin": 2_000,
        "underlying_pre_close": 3.05,
    }


CHAIN = [
    contract("NC300", "call", 3.0, "20260826"),
    contract("NC310", "call", 3.1, "20260826"),
    contract("NP300", "put", 3.0, "20260826"),
    contract("NP310", "put", 3.1, "20260826"),
    contract("FC300", "call", 3.0, "20270324"),
    contract("FC310", "call", 3.1, "20270324"),
]


class FakeOptionDataSource:
    backend = "fixture"

    def __init__(self):
        self.daily_symbols = set()
        self.iv_symbols = set()
        self.risk_symbols = set()

    def latest_nonempty_static(self, underlying, as_of, max_lookback_days=5):
        return CHAIN, "20260724", [as_of, "20260724"]

    def option_daily(self, start, end, symbol=None):
        self.daily_symbols = set(symbol or [])
        return [
            {"date": end, "symbol": sym, "close": 0.08 if sym.startswith("N") else 0.20}
            for sym in self.daily_symbols
        ]

    def option_iv(self, start, end, symbol=None):
        self.iv_symbols = set(symbol or [])
        return [
            {"date": end, "symbol": sym, "implied_volatility": 20.0}
            for sym in self.iv_symbols
        ]

    def option_risk_indicators(self, start, end, symbol=None):
        self.risk_symbols = set(symbol or [])
        return [
            {
                "date": end, "symbol": sym,
                "delta": 0.5, "gamma": 0.1, "vega": 0.2,
                "theta": -0.1, "rho": 0.1,
            }
            for sym in self.risk_symbols
        ]


def priced(leg, premium, sigma=0.2):
    return {
        "leg": leg,
        "premium": premium,
        "sigma": sigma,
        "greeks": {"delta": 0.5, "gamma": 0.1, "vega": 0.2, "theta": -0.1, "rho": 0.1},
    }


class OptionRegressionTests(unittest.TestCase):
    def test_single_expiry_strategy_never_mixes_expiries(self):
        selected, _ = legs.select_legs(
            CHAIN, 3.05, "vertical_spread", "bullish", expiry="20260826"
        )
        self.assertEqual({leg["expiry"] for leg in selected}, {"20260826"})

    def test_calendar_requires_two_expiries(self):
        selected, notes = legs.select_legs(
            [row for row in CHAIN if row["delisted_date"] == "20260826"],
            3.05,
            "calendar",
        )
        self.assertEqual(selected, [])
        self.assertTrue(any("两个到期月" in note for note in notes))

    def test_custom_without_legs_fails_instead_of_becoming_vertical(self):
        selected, notes = legs.select_legs(CHAIN, 3.05, "custom", user_legs=None)
        self.assertEqual(selected, [])
        self.assertTrue(any("必须提供" in note for note in notes))

    def test_iv_percent_and_ratio_normalize_equally(self):
        self.assertEqual(pricing.normalize_iv(20.0), 0.20)
        self.assertEqual(pricing.normalize_iv(0.20), 0.20)

    def test_covered_call_underlying_bounds_upside_and_downside(self):
        option = legs._leg(legs._norm_chain(CHAIN)[1], "short")
        underlying = legs.underlying_leg(3.05, option["contract_size"], option["expiry"])
        plegs = [priced(underlying, 0.0), priced(option, 0.04)]
        cashflow = payoff.leg_cashflow_premium(plegs, 1)
        summary = payoff.payoff_summary(plegs, 1, cashflow)
        self.assertFalse(summary["max_profit_unbounded"])
        self.assertFalse(summary["max_loss_unbounded"])
        self.assertLess(summary["max_loss"], -20_000)

    def test_long_straddle_profit_is_unbounded_and_loss_is_finite(self):
        normalized = legs._norm_chain(CHAIN)
        call = legs._leg(next(row for row in normalized
                             if row["type"] == "call" and row["strike"] == 3.0), "long")
        put = legs._leg(next(row for row in normalized
                            if row["type"] == "put" and row["strike"] == 3.0), "long")
        plegs = [priced(call, 0.10), priced(put, 0.08)]
        cashflow = payoff.leg_cashflow_premium(plegs, 1)
        summary = payoff.payoff_summary(plegs, 1, cashflow)
        self.assertTrue(summary["max_profit_unbounded"])
        self.assertFalse(summary["max_loss_unbounded"])

    def test_calendar_curve_revalues_far_leg(self):
        normalized = legs._norm_chain(CHAIN)
        near = legs._leg(next(row for row in normalized if row["symbol"] == "NC300"), "short")
        far = legs._leg(next(row for row in normalized if row["symbol"] == "FC300"), "long")
        plegs = [priced(near, 0.08), priced(far, 0.20, sigma=0.22)]
        cashflow = payoff.leg_cashflow_premium(plegs, 1)
        curve = payoff.calendar_payoff_curve(
            plegs, 1, 3.05, cashflow, "20260826", rate=0.02
        )
        self.assertGreater(len({point["payoff"] for point in curve}), 3)

    def test_card_queries_only_selected_symbols_and_records_dates(self):
        fake = FakeOptionDataSource()
        card = strategy_card.build_card(
            "510050.SH", "vertical_spread", view="bullish",
            as_of="20260728", expiry="20260826", data_source=fake,
        )
        symbols = {leg["symbol"] for leg in card["legs"] if leg["type"] != "underlying"}
        self.assertEqual(fake.daily_symbols, symbols)
        self.assertEqual(fake.iv_symbols, symbols)
        self.assertEqual(fake.risk_symbols, symbols)
        self.assertEqual(card["data_date"], "20260724")
        self.assertIn("margin_est", card)
        self.assertTrue(all(leg["price_date"] for leg in card["legs"]))

    def test_calendar_card_uses_near_expiry_valuation(self):
        card = strategy_card.build_card(
            "510050.SH", "calendar", as_of="20260728",
            near_expiry="20260826", far_expiry="20270324",
            data_source=FakeOptionDataSource(),
        )
        self.assertEqual(card["status"], "ok")
        self.assertEqual(card["valuation_date"], "20260826")
        self.assertEqual(
            card["valuation_method"], "near_expiry_far_leg_bs_revaluation"
        )
        self.assertGreater(len({point["payoff"] for point in card["payoff_curve"]}), 3)

    def test_custom_without_legs_returns_failed_card(self):
        card = strategy_card.build_card(
            "510050.SH", "custom", as_of="20260728",
            data_source=FakeOptionDataSource(),
        )
        self.assertEqual(card["status"], "failed")
        self.assertEqual(card["legs"], [])


if __name__ == "__main__":
    unittest.main()
