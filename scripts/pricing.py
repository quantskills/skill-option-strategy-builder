"""
pricing.py — 期权定价与希腊字母（纯标准库）

职责：
  1) BS 模型（用 math.erf 实现正态CDF，不依赖 numpy/scipy）——用于缺失希腊字母时补算。
  2) 单腿希腊字母：优先用接口 get_option_risk_indicators 的真实值；缺任一字段用 BS 补算并声明。
  3) 组合净希腊字母：各腿按方向(+买/-卖)与合约数加总。

希腊字母口径（单位标的、每份合约、年化）：
  - delta：无量纲，认购 0~1、认沽 -1~0。
  - gamma：delta 对标的价的变化率。
  - vega：波动率变动 1（即 100%）时权利金变化；报告里再折算成 IV 变 1% 的影响。
  - theta：每年时间衰减（负=每天损耗）；报告里可折算成每日。
  - rho：无风险利率变动 1（100%）时权利金变化。
"""
from __future__ import annotations
import math

SQRT2 = math.sqrt(2.0)
SQRT2PI = math.sqrt(2.0 * math.pi)


def norm_cdf(x: float) -> float:
    """标准正态累积分布函数 N(x)，用 math.erf 实现。"""
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def norm_pdf(x: float) -> float:
    """标准正态概率密度函数 n(x)。"""
    return math.exp(-0.5 * x * x) / SQRT2PI


def bs_price_greeks(S, K, T, sigma, r=0.02, q=0.0, is_call=True) -> dict:
    """
    Black-Scholes 定价 + 五个希腊字母（欧式，连续分红率 q）。

    参数：
      S 标的现价, K 行权价, T 到期年数, sigma 年化波动率, r 无风险利率, q 分红率。
    返回 dict：price/delta/gamma/vega/theta/rho（vega/rho 对应变量变动 1.0，即 100%）。
    """
    # 边界：到期或无波动，退化为内在价值。
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        if is_call:
            price = max(S - K, 0.0)
            delta = 1.0 if S > K else 0.0
        else:
            price = max(K - S, 0.0)
            delta = -1.0 if S < K else 0.0
        return {"price": round(price, 6), "delta": round(delta, 6),
                "gamma": 0.0, "vega": 0.0, "theta": 0.0, "rho": 0.0}

    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    disc_r = math.exp(-r * T)
    disc_q = math.exp(-q * T)
    pdf_d1 = norm_pdf(d1)

    if is_call:
        price = S * disc_q * norm_cdf(d1) - K * disc_r * norm_cdf(d2)
        delta = disc_q * norm_cdf(d1)
        theta = (-S * disc_q * pdf_d1 * sigma / (2.0 * math.sqrt(T))
                 - r * K * disc_r * norm_cdf(d2)
                 + q * S * disc_q * norm_cdf(d1))
        rho = K * T * disc_r * norm_cdf(d2)
    else:
        price = K * disc_r * norm_cdf(-d2) - S * disc_q * norm_cdf(-d1)
        delta = -disc_q * norm_cdf(-d1)
        theta = (-S * disc_q * pdf_d1 * sigma / (2.0 * math.sqrt(T))
                 + r * K * disc_r * norm_cdf(-d2)
                 - q * S * disc_q * norm_cdf(-d1))
        rho = -K * T * disc_r * norm_cdf(-d2)

    gamma = disc_q * pdf_d1 / (S * sigma * math.sqrt(T))
    vega = S * disc_q * pdf_d1 * math.sqrt(T)   # 对 sigma 变动 1.0（100%）

    return {"price": round(price, 6), "delta": round(delta, 6),
            "gamma": round(gamma, 6), "vega": round(vega, 6),
            "theta": round(theta, 6), "rho": round(rho, 6)}


GREEK_KEYS = ("delta", "gamma", "vega", "theta", "rho")


def normalize_iv(value) -> float:
    """Normalize interface IV to decimal form, accepting either 20 or 0.20."""
    iv = float(value)
    if iv <= 0:
        raise ValueError("IV must be positive")
    if iv > 3.0:
        iv /= 100.0
    if iv > 5.0:
        raise ValueError("IV exceeds 500%")
    return round(iv, 8)


def implied_volatility_from_price(option_type, market_price, spot, strike,
                                  time_years, rate=0.02) -> float | None:
    """Invert Black-Scholes price with bounded bisection."""
    target = float(market_price)
    if target <= 0 or spot <= 0 or strike <= 0 or time_years <= 0:
        return None
    is_call = option_type == "call"
    lo, hi = 1e-4, 5.0
    p_lo = bs_price_greeks(spot, strike, time_years, lo, r=rate, is_call=is_call)["price"]
    p_hi = bs_price_greeks(spot, strike, time_years, hi, r=rate, is_call=is_call)["price"]
    if not (p_lo <= target <= p_hi):
        return None
    for _ in range(80):
        mid = (lo + hi) / 2.0
        price = bs_price_greeks(
            spot, strike, time_years, mid, r=rate, is_call=is_call
        )["price"]
        if price < target:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2.0, 8)


def leg_greeks(leg, risk_row, S, T, sigma, r=0.02, q=0.0) -> dict:
    """
    单腿希腊字母：优先用接口 risk_row 的真实值；任一为 None 用 BS 补算。
    返回 {"greeks": {...}, "source": "interface"/"bs"/"mixed", "bs_filled": [缺失字段]}。
    """
    is_call = leg["type"] == "call"
    K = leg["strike"]
    bs = bs_price_greeks(S, K, T, sigma, r=r, q=q, is_call=is_call)

    greeks = {}
    filled = []
    for k in GREEK_KEYS:
        v = (risk_row or {}).get(k)
        if v is None:
            greeks[k] = bs[k]
            filled.append(k)
        else:
            greeks[k] = float(v)

    if not filled:
        source = "interface"
    elif len(filled) == len(GREEK_KEYS):
        source = "bs"
    else:
        source = "mixed"
    return {"greeks": greeks, "source": source, "bs_filled": filled, "bs_price": bs["price"]}


def net_greeks(priced_legs, contracts, contract_size) -> dict:
    """组合净希腊字母：Σ 方向(+买/-卖) × 数量 × 合约数 × 合约单位 × 单腿希腊字母。"""
    net = {k: 0.0 for k in GREEK_KEYS}
    for pl in priced_legs:
        leg = pl["leg"]
        side = 1.0 if leg["side"] == "long" else -1.0
        qty = leg.get("qty", 1)
        leg_contract_size = leg.get("contract_size") or contract_size
        mult = side * qty * contracts * leg_contract_size
        if leg["type"] == "underlying":
            net["delta"] += mult
            continue
        for k in GREEK_KEYS:
            net[k] += mult * pl["greeks"][k]
    return {k: round(v, 4) for k, v in net.items()}


def leg_premium(leg, daily_map, bs_price) -> float:
    """
    单腿权利金（单位标的价，未乘合约单位/数量）：优先用日线 close/settlement；缺则用 BS 价。
    """
    sym = leg.get("symbol")
    row = daily_map.get(sym) if sym else None
    if row is not None:
        px = row.get("close")
        if px is None:
            px = row.get("settlement")
        if px is not None:
            return float(px)
    return float(bs_price)
