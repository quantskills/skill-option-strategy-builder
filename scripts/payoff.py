"""
payoff.py — 到期损益曲线 / 盈亏平衡点 / 最大盈亏 / 保证金

约定（金额单位：元，已乘合约单位与合约数）：
  - 净权利金 net_premium：买腿付出(负现金流)、卖腿收入(正现金流) 的净额。
    net_premium > 0 表示净收权利金(贷记)；< 0 表示净付权利金(借记)。
  - 到期损益 payoff(S) = Σ 各腿到期内在价值现金流 + 期初净权利金现金流。
  - 保证金：优先用 get_option_static.margin(单位保证金/张)；缺则用规则估算。
"""
from __future__ import annotations
from datetime import datetime

import pricing


def _leg_intrinsic(leg, S):
    """单腿到期内在价值（单位标的价，未乘合约单位/数量/方向）。"""
    if leg["type"] == "underlying":
        return S - leg["entry_price"]
    K = leg["strike"]
    if leg["type"] == "call":
        return max(S - K, 0.0)
    return max(K - S, 0.0)


def leg_cashflow_premium(priced_legs, contracts):
    """
    期初权利金现金流（元，正=收入/卖腿，负=支出/买腿）。
    priced_legs 每项含 leg 与 premium（单位标的价）。
    """
    total = 0.0
    for pl in priced_legs:
        leg = pl["leg"]
        if leg["type"] == "underlying":
            continue
        side = 1.0 if leg["side"] == "long" else -1.0
        cs = leg.get("contract_size") or 10000
        qty = leg.get("qty", 1)
        # 买腿付钱(负)、卖腿收钱(正) → 现金流 = -side * premium
        total += (-side) * pl["premium"] * cs * qty * contracts
    return round(total, 2)


def payoff_at(priced_legs, contracts, S, net_premium_cashflow):
    """到期总损益(元) at 标的价 S = Σ 各腿内在价值现金流 + 期初净权利金现金流。"""
    total = net_premium_cashflow
    for pl in priced_legs:
        leg = pl["leg"]
        side = 1.0 if leg["side"] == "long" else -1.0
        cs = leg.get("contract_size") or 10000
        qty = leg.get("qty", 1)
        total += side * _leg_intrinsic(leg, S) * cs * qty * contracts
    return round(total, 2)


def build_payoff_curve(priced_legs, contracts, spot, net_premium_cashflow, n=41, span=0.30):
    """在 [spot*(1-span), spot*(1+span)] 上均匀取 n 个价位算到期损益。"""
    lo, hi = spot * (1 - span), spot * (1 + span)
    step = (hi - lo) / (n - 1)
    curve = []
    for i in range(n):
        S = lo + i * step
        curve.append({"S": round(S, 4), "payoff": payoff_at(priced_legs, contracts, S, net_premium_cashflow)})
    return curve


def find_breakevens(curve):
    """损益曲线过零点（线性插值）即盈亏平衡点。"""
    bes = []
    for a, b in zip(curve, curve[1:]):
        y0, y1 = a["payoff"], b["payoff"]
        if y0 == 0:
            bes.append(round(a["S"], 4))
        elif (y0 < 0 < y1) or (y0 > 0 > y1):
            x0, x1 = a["S"], b["S"]
            be = x0 + (0 - y0) * (x1 - x0) / (y1 - y0)
            bes.append(round(be, 4))
    # 去重
    uniq = []
    for x in bes:
        if not uniq or abs(uniq[-1] - x) > 1e-6:
            uniq.append(x)
    return uniq


def max_profit_loss(curve):
    """
    从损益曲线取样本内最大盈利/最大亏损。
    naked/单边卖出等理论无界情形，在曲线端点体现并标注 unbounded。
    """
    payoffs = [p["payoff"] for p in curve]
    mp = max(payoffs)
    ml = min(payoffs)
    # 端点趋势判断是否可能无界
    up_unbounded = curve[-1]["payoff"] > curve[-2]["payoff"] and curve[-1]["payoff"] == mp
    down_unbounded = curve[0]["payoff"] < curve[1]["payoff"] and curve[0]["payoff"] == ml
    return {
        "max_profit": round(mp, 2),
        "max_loss": round(ml, 2),
        "max_profit_unbounded": bool(up_unbounded),
        "max_loss_unbounded": bool(down_unbounded),
    }


def payoff_summary(priced_legs, contracts, net_premium_cashflow):
    """Exact extrema and roots for same-expiry piecewise-linear positions."""
    strikes = sorted({
        float(pl["leg"]["strike"])
        for pl in priced_legs
        if pl["leg"]["type"] in ("call", "put")
    })
    points = [0.0] + strikes
    values = [
        payoff_at(priced_legs, contracts, point, net_premium_cashflow)
        for point in points
    ]
    breakevens = []
    for x0, x1, y0, y1 in zip(points, points[1:], values, values[1:]):
        if y0 == 0:
            breakevens.append(round(x0, 4))
        if y0 * y1 < 0:
            breakevens.append(round(x0 - y0 * (x1 - x0) / (y1 - y0), 4))
    if values and values[-1] == 0:
        breakevens.append(round(points[-1], 4))

    right_slope = 0.0
    for pl in priced_legs:
        leg = pl["leg"]
        side = 1.0 if leg["side"] == "long" else -1.0
        mult = side * (leg.get("contract_size") or 10000) * leg.get("qty", 1) * contracts
        if leg["type"] in ("call", "underlying"):
            right_slope += mult
    if points and right_slope:
        root = points[-1] - values[-1] / right_slope
        if root > points[-1]:
            breakevens.append(round(root, 4))

    breakevens = sorted(set(breakevens))
    return {
        "breakevens": breakevens,
        "max_profit": round(max(values), 2),
        "max_loss": round(min(values), 2),
        "max_profit_unbounded": right_slope > 0,
        "max_loss_unbounded": right_slope < 0,
    }


def calendar_payoff_curve(priced_legs, contracts, spot, net_premium_cashflow,
                          near_expiry, rate=0.02, points=401, span=0.50):
    """Value a calendar spread at near expiry, retaining far-leg time value."""
    far_expiries = [
        str(pl["leg"]["expiry"]) for pl in priced_legs
        if pl["leg"]["type"] in ("call", "put")
        and str(pl["leg"]["expiry"]) != str(near_expiry)
    ]
    if not far_expiries:
        raise ValueError("calendar spread requires a far-expiry leg")
    near_date = datetime.strptime(str(near_expiry), "%Y%m%d")
    far_date = datetime.strptime(max(far_expiries), "%Y%m%d")
    remaining = max((far_date - near_date).days, 1) / 365.0
    lo, hi = spot * (1 - span), spot * (1 + span)
    curve = []
    for index in range(points):
        S = lo + (hi - lo) * index / (points - 1)
        total = net_premium_cashflow
        for pl in priced_legs:
            leg = pl["leg"]
            side = 1.0 if leg["side"] == "long" else -1.0
            mult = (leg.get("contract_size") or 10000) * leg.get("qty", 1) * contracts
            if str(leg["expiry"]) == str(near_expiry):
                value = _leg_intrinsic(leg, S)
            else:
                value = pricing.bs_price_greeks(
                    S, leg["strike"], remaining, pl["sigma"], r=rate,
                    is_call=leg["type"] == "call",
                )["price"]
            total += side * value * mult
        curve.append({"S": round(S, 4), "payoff": round(total, 2)})
    return curve


def estimate_margin(priced_legs, contracts):
    """
    保证金占用估算（元）：只对卖(short)腿计。
    优先用 get_option_static.margin(单位保证金/张)；缺则用规则估算：
      单位保证金 ≈ 权利金 + 12% × 标的价 × 合约单位（简化交易所公式）。
    """
    total = 0.0
    detail = []
    for pl in priced_legs:
        leg = pl["leg"]
        if leg["type"] == "underlying":
            continue
        if leg["side"] != "short":
            continue
        cs = leg.get("contract_size") or 10000
        qty = leg.get("qty", 1)
        m_unit = leg.get("margin")
        if m_unit is not None:
            m = float(m_unit) * qty * contracts
            src = "interface_margin"
        else:
            spot_proxy = pl.get("spot", leg["strike"])
            est_unit = (pl["premium"] + 0.12 * spot_proxy) * cs
            m = est_unit * qty * contracts
            src = "rule_estimate"
        total += m
        detail.append({"symbol": leg["symbol"], "strike": leg["strike"], "margin": round(m, 2), "source": src})
    return {"margin_total": round(total, 2), "legs": detail}
