#!/usr/bin/env python3
"""
strategy_card.py — 期权策略构建器 CLI 入口

用法:
  python scripts/strategy_card.py --underlying 510050.SH --type vertical_spread \
      --view bullish --contracts 1 --out card.json --md card.md

无凭证自动回退内置样本（examples/sample_data/）。
"""
from __future__ import annotations
import argparse
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_source import DataSource   # noqa: E402
import legs as legmod                 # noqa: E402
import pricing as pr                  # noqa: E402
import payoff as po                   # noqa: E402
import formatters                     # noqa: E402

STRATEGY_CN = {
    "vertical_spread": "垂直价差", "straddle": "跨式", "strangle": "宽跨式",
    "collar": "领口", "calendar": "日历价差", "covered_call": "备兑看涨", "custom": "自定义腿",
}
VIEW_CN = {"bullish": "看涨", "bearish": "看跌", "neutral": "中性/看波动"}


def _year_frac(today_str, expiry_str) -> float:
    """到期年数 T：expiry(YYYYMMDD) - today，最少给 1 天避免除零。"""
    try:
        d0 = datetime.strptime(today_str, "%Y%m%d")
        d1 = datetime.strptime(str(expiry_str), "%Y%m%d")
        days = max((d1 - d0).days, 1)
        return days / 365.0
    except Exception:
        return 30.0 / 365.0


def _latest_by_symbol(rows, key="date"):
    """把日线/风险/IV 行按 symbol 归集，取最新一条。"""
    out = {}
    for r in rows:
        sym = r.get("symbol")
        if not sym:
            continue
        if sym not in out or str(r.get(key, "")) > str(out[sym].get(key, "")):
            out[sym] = r
    return out


def build_card(underlying, strategy_type, view="neutral", contracts=1,
               user_legs=None, r=0.02, prefer=None, as_of=None, expiry=None,
               near_expiry=None, far_expiry=None, data_source=None) -> dict:
    ds = data_source or DataSource(prefer=prefer)
    now = datetime.now()
    requested_date = as_of or now.strftime("%Y%m%d")
    degraded, errors = [], []
    static_rows, data_date, attempted = ds.latest_nonempty_static(
        underlying, requested_date, max_lookback_days=5
    )
    if not static_rows:
        errors.append(
            f"{underlying}: {requested_date} 向前 5 日无 get_option_static 数据"
        )

    spot = next(
        (float(row["underlying_pre_close"]) for row in static_rows
         if row.get("underlying_pre_close") is not None),
        None,
    )
    if spot is None:
        errors.append("标的现价缺失，拒绝使用占位值")

    selected_legs, notes = ([], [])
    if static_rows and spot is not None:
        selected_legs, notes = legmod.select_legs(
            static_rows, spot, strategy_type, view, user_legs,
            expiry=expiry, near_expiry=near_expiry, far_expiry=far_expiry,
        )
    if not selected_legs:
        errors.extend(notes or ["未能选出策略腿"])

    option_legs = [leg for leg in selected_legs if leg["type"] != "underlying"]
    leg_syms = [leg["symbol"] for leg in option_legs]
    daily_map = _latest_by_symbol(
        ds.option_daily(data_date, data_date, symbol=leg_syms)
    ) if data_date and leg_syms else {}
    risk_map = _latest_by_symbol(
        ds.option_risk_indicators(data_date, data_date, symbol=leg_syms)
    ) if data_date and leg_syms else {}
    iv_map = _latest_by_symbol(
        ds.option_iv(data_date, data_date, symbol=leg_syms)
    ) if data_date and leg_syms else {}

    priced_legs = []
    for leg in selected_legs:
        if leg["type"] == "underlying":
            priced_legs.append({
                "leg": leg,
                "greeks": {"delta": 1.0, "gamma": 0.0, "vega": 0.0,
                           "theta": 0.0, "rho": 0.0},
                "greeks_source": "underlying",
                "bs_filled": [],
                "premium": 0.0,
                "premium_source": "not_applicable",
                "sigma": None,
                "T": _year_frac(data_date, leg["expiry"]),
                "spot": spot,
                "price_date": data_date,
                "iv_date": None,
                "greeks_date": data_date,
            })
            continue

        symbol = leg["symbol"]
        daily_row = daily_map.get(symbol)
        premium = None
        if daily_row:
            premium = daily_row.get("close")
            if premium is None:
                premium = daily_row.get("settlement")
        if premium is None:
            errors.append(f"{symbol}: 缺真实期权价格")
            continue
        premium = float(premium)
        T = _year_frac(data_date, leg["expiry"])
        iv_row = iv_map.get(symbol)
        sigma = None
        if iv_row and iv_row.get("implied_volatility") is not None:
            try:
                sigma = pr.normalize_iv(iv_row["implied_volatility"])
            except ValueError as exc:
                degraded.append(f"{symbol}: IV 无效（{exc}），尝试由真实价格反解")
        if sigma is None:
            sigma = pr.implied_volatility_from_price(
                leg["type"], premium, spot, leg["strike"], T, rate=r
            )
            if sigma is None:
                errors.append(f"{symbol}: 无可靠 IV，且无法由真实价格反解")
                continue
            degraded.append(f"{symbol}: 接口 IV 缺失，已由真实期权价格反解")

        risk_row = risk_map.get(symbol)
        greek_result = pr.leg_greeks(leg, risk_row, spot, T, sigma, r=r)
        if greek_result["bs_filled"]:
            degraded.append(
                f"{symbol}: 希腊字母缺失，BS 补算 "
                + ", ".join(greek_result["bs_filled"])
            )
        priced_legs.append({
            "leg": leg,
            "greeks": greek_result["greeks"],
            "greeks_source": greek_result["source"],
            "bs_filled": greek_result["bs_filled"],
            "premium": premium,
            "premium_source": "get_option_daily",
            "sigma": sigma,
            "T": T,
            "spot": spot,
            "price_date": daily_row.get("date") if daily_row else None,
            "iv_date": iv_row.get("date") if iv_row else data_date,
            "greeks_date": risk_row.get("date") if risk_row else None,
        })

    if len(priced_legs) != len(selected_legs):
        errors.append("部分策略腿缺少可靠定价数据")

    contract_size = (
        option_legs[0].get("contract_size") or 10000
        if option_legs else 10000
    )
    empty_greeks = {key: 0.0 for key in pr.GREEK_KEYS}
    net_premium = 0.0
    curve, breakevens = [], []
    extrema = {
        "max_profit": None, "max_loss": None,
        "max_profit_unbounded": False, "max_loss_unbounded": False,
    }
    model_range = None
    valuation_date = None
    valuation_method = None
    margin = {"margin_total": 0.0, "legs": []}
    net_greeks = empty_greeks

    if not errors:
        net_premium = po.leg_cashflow_premium(priced_legs, contracts)
        if strategy_type == "calendar":
            valuation_date = min(leg["expiry"] for leg in option_legs)
            valuation_method = "near_expiry_far_leg_bs_revaluation"
            curve = po.calendar_payoff_curve(
                priced_legs, contracts, spot, net_premium,
                valuation_date, rate=r,
            )
            breakevens = po.find_breakevens(curve)
            values = [point["payoff"] for point in curve]
            extrema["max_profit"] = round(max(values), 2)
            extrema["max_loss"] = round(min(values), 2)
            model_range = {
                "spot_min": curve[0]["S"], "spot_max": curve[-1]["S"],
                "max_profit": extrema["max_profit"], "max_loss": extrema["max_loss"],
            }
        else:
            valuation_date = option_legs[0]["expiry"] if option_legs else data_date
            valuation_method = "expiry_piecewise_linear"
            curve = po.build_payoff_curve(
                priced_legs, contracts, spot, net_premium, n=401, span=0.50
            )
            summary = po.payoff_summary(priced_legs, contracts, net_premium)
            breakevens = summary.pop("breakevens")
            extrema.update(summary)
        net_greeks = pr.net_greeks(priced_legs, contracts, contract_size)
        margin = po.estimate_margin(priced_legs, contracts)

    status = "failed" if errors else "degraded" if degraded else "ok"
    sigmas = [pl["sigma"] for pl in priced_legs if pl["sigma"] is not None]
    return {
        "status": status,
        "underlying": underlying,
        "strategy_type": strategy_type,
        "strategy_type_cn": STRATEGY_CN.get(strategy_type, strategy_type),
        "view": view, "view_cn": VIEW_CN.get(view, view),
        "contracts": contracts, "backend": ds.backend,
        "generated_at": now.strftime("%Y-%m-%d %H:%M"),
        "requested_date": requested_date, "data_date": data_date,
        "attempted_dates": attempted, "valuation_date": valuation_date,
        "valuation_method": valuation_method,
        "spot": round(spot, 4) if spot is not None else None,
        "sigma_used": round(sum(sigmas) / len(sigmas), 4) if sigmas else None,
        "T_years": round(max((pl["T"] for pl in priced_legs), default=0.0), 4),
        "legs": [{
            "symbol": pl["leg"]["symbol"], "type": pl["leg"]["type"],
            "side": pl["leg"]["side"], "strike": pl["leg"].get("strike"),
            "entry_price": pl["leg"].get("entry_price"),
            "qty": pl["leg"]["qty"], "expiry": pl["leg"]["expiry"],
            "contract_size": pl["leg"].get("contract_size"),
            "premium": round(pl["premium"], 4),
            "premium_source": pl["premium_source"],
            "iv": pl["sigma"],
            "greeks_source": pl["greeks_source"], "greeks": pl["greeks"],
            "margin": pl["leg"].get("margin"),
            "price_date": pl["price_date"], "iv_date": pl["iv_date"],
            "greeks_date": pl["greeks_date"],
        } for pl in priced_legs],
        "net_premium": net_premium,
        "breakevens": breakevens,
        **extrema,
        "net_greeks": net_greeks,
        "margin_est": margin, "margin": margin,
        "payoff_curve": curve,
        "model_range": model_range,
        "sources": {
            "contracts": "get_option_static",
            "prices": "get_option_daily",
            "iv": "get_option_implied_volatility_or_price_inversion",
            "greeks": "get_option_risk_indicators_or_bs",
        },
        "notes": notes,
        "degraded": degraded,
        "errors": errors,
    }


def main():
    ap = argparse.ArgumentParser(description="期权策略构建器：损益/希腊/保证金策略卡")
    ap.add_argument("--underlying", required=True, help="期权标的，如 510050.SH")
    ap.add_argument("--type", dest="strategy_type", default="vertical_spread",
                    choices=list(STRATEGY_CN.keys()))
    ap.add_argument("--view", default="neutral", choices=list(VIEW_CN.keys()))
    ap.add_argument("--contracts", type=int, default=1)
    ap.add_argument("--rate", type=float, default=0.02, help="无风险利率(年化)")
    ap.add_argument("--as-of", default=None, help="请求数据日期 YYYYMMDD；空数据最多回退 5 日")
    ap.add_argument("--expiry", default=None, help="普通结构到期月 YYYYMMDD")
    ap.add_argument("--near-expiry", default=None, help="日历价差近月 YYYYMMDD")
    ap.add_argument("--far-expiry", default=None, help="日历价差远月 YYYYMMDD")
    ap.add_argument("--legs-json", default=None, help="custom 策略腿 JSON 文件")
    ap.add_argument("--prefer", choices=["sdk", "sample"], default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--md", default=None)
    args = ap.parse_args()

    user_legs = None
    if args.legs_json:
        try:
            user_legs = json.loads(Path(args.legs_json).read_text(encoding="utf-8"))
            if not isinstance(user_legs, list):
                raise ValueError("根节点必须是 list")
        except Exception as exc:  # noqa: BLE001
            print(f"自定义腿文件无效: {exc}", file=sys.stderr)
            return 2
    card = build_card(
        args.underlying, args.strategy_type, args.view, args.contracts,
        user_legs=user_legs, r=args.rate, prefer=args.prefer,
        as_of=args.as_of, expiry=args.expiry,
        near_expiry=args.near_expiry, far_expiry=args.far_expiry,
    )
    print(formatters.to_text(card))
    if args.out:
        Path(args.out).write_text(formatters.to_json(card), encoding="utf-8")
        print(f"\n[已写出 JSON] {args.out}")
    if args.md:
        Path(args.md).write_text(formatters.to_markdown(card), encoding="utf-8")
        print(f"[已写出 Markdown] {args.md}")
    return 2 if card["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
