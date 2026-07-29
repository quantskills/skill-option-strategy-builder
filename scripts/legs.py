"""
legs.py — 按结构 + 方向观点自动选腿

输入：期权链（get_option_static 行，含 strike_price/call_put_code/delisted_date/
contract_size/margin/symbol），标的现价，结构类型，方向观点。
输出：legs[]，每腿 {symbol, type(call/put), side(long/short), strike, qty, expiry, meta}。

选腿规则（详见 references/strategies.md）：
  - vertical_spread 牛市: 买 ATM call + 卖 OTM call（看涨,借记）
                    熊市: 买 ATM put + 卖 OTM put（看跌,借记）
  - straddle 跨式: 买 ATM call + 买 ATM put（看波动放大）
  - strangle 宽跨式: 买 OTM call + 买 OTM put
  - collar 领口: 持有标的 + 买 OTM put + 卖 OTM call（保护性,零成本区间）
  - calendar 日历价差: 卖近月 ATM + 买远月 ATM（同行权价,不同到期）
  - covered_call 备兑看涨: 持有标的 + 卖 OTM call
"""
from __future__ import annotations

CO, PO = "CO", "PO"   # 接口 call_put_code：CO=认购(call)，PO=认沽(put)


def _norm_chain(static_rows) -> list[dict]:
    """把 get_option_static 行标准化为内部合约表示。"""
    out = []
    for r in static_rows:
        cp = r.get("call_put_code")
        typ = "call" if cp == CO else "put" if cp == PO else None
        sk = r.get("strike_price")
        if typ is None or sk is None:
            continue
        out.append({
            "symbol": r.get("symbol"),
            "type": typ,
            "strike": float(sk),
            "expiry": r.get("delisted_date"),
            "contract_size": r.get("contract_size") or 10000,
            "margin": r.get("margin"),
            "underlying_pre_close": r.get("underlying_pre_close"),
        })
    return out


def _atm(contracts_of_type, spot):
    """选行权价最接近现价的合约（ATM）。"""
    return min(contracts_of_type, key=lambda c: abs(c["strike"] - spot))


def _otm_call(calls, spot, step=1):
    """选虚值认购：行权价 > 现价，按行权价升序取第 step 档。"""
    higher = sorted([c for c in calls if c["strike"] > spot], key=lambda c: c["strike"])
    if not higher:
        return sorted(calls, key=lambda c: c["strike"])[-1]
    return higher[min(step - 1, len(higher) - 1)]


def _otm_put(puts, spot, step=1):
    """选虚值认沽：行权价 < 现价，按行权价降序取第 step 档。"""
    lower = sorted([p for p in puts if p["strike"] < spot], key=lambda p: -p["strike"])
    if not lower:
        return sorted(puts, key=lambda p: p["strike"])[0]
    return lower[min(step - 1, len(lower) - 1)]


def _spread_pair(contracts, spot, bull=True):
    """
    为垂直价差选相邻两档行权价，保证两腿 strike 不同（价差策略的前提）。
    返回 (低行权价合约, 高行权价合约)。
    - 牛市(bull=True): 买低卖高 → 调用方用 (lo, hi)。
    - 熊市(bull=False): 买高卖低 → 调用方用 (hi, lo)。
    选档策略：以最接近现价的行权价为锚，取锚及其相邻一档组成价差；
    若现价落在两档之间，取跨现价的两档（更贴近 ATM 价差）。
    """
    uniq = sorted({c["strike"]: c for c in contracts}.values(), key=lambda c: c["strike"])
    if len(uniq) < 2:
        return uniq[0], uniq[0]
    # 找最接近现价的档位索引
    i = min(range(len(uniq)), key=lambda k: abs(uniq[k]["strike"] - spot))
    # 取锚 + 相邻档：优先跨现价，否则用锚与其上一档/下一档
    if i + 1 < len(uniq) and (uniq[i]["strike"] <= spot or i == 0):
        lo, hi = uniq[i], uniq[i + 1]
    else:
        lo, hi = uniq[i - 1], uniq[i]
    return lo, hi


def _leg(contract, side, qty=1):
    return {
        "symbol": contract["symbol"], "type": contract["type"], "side": side,
        "strike": contract["strike"], "qty": qty, "expiry": contract["expiry"],
        "contract_size": contract["contract_size"], "margin": contract.get("margin"),
    }


def underlying_leg(spot, contract_size, expiry):
    """Represent the stock/ETF holding required by collar and covered-call structures."""
    return {
        "symbol": "UNDERLYING", "type": "underlying", "side": "long",
        "strike": None, "entry_price": float(spot), "qty": 1, "expiry": expiry,
        "contract_size": contract_size or 10000, "margin": None,
    }


def select_legs(static_rows, spot, strategy_type, view="neutral", user_legs=None,
                expiry=None, near_expiry=None, far_expiry=None):
    """
    返回 (legs, notes)。notes 记录选腿理由/降级说明。
    日历价差必须有两个到期月；任何结构不完整时返回空腿，绝不替换策略。
    """
    chain = _norm_chain(static_rows)
    notes = []
    expiries = sorted({c["expiry"] for c in chain if c["expiry"]})

    if strategy_type == "custom" and not user_legs:
        return [], ["自定义策略必须提供 user_legs/--legs-json"]

    if strategy_type == "custom" and user_legs:
        legs = []
        for ul in user_legs:
            candidates = [
                c for c in chain
                if c["type"] == ul["type"]
                and c["strike"] == float(ul["strike"])
                and (not ul.get("expiry") or c["expiry"] == ul["expiry"])
            ]
            c = candidates[0] if len(candidates) == 1 else None
            if c is None:
                notes.append(f"自定义腿 {ul} 未唯一匹配期权链")
                return [], notes
            legs.append(_leg(c, ul.get("side", "long"), ul.get("qty", 1)))
        return legs, notes

    if strategy_type == "calendar":
        if len(expiries) < 2:
            return [], ["日历价差必须提供两个到期月，当前合约链不足"]
        near = near_expiry or expiries[0]
        far = far_expiry or expiries[1]
        if near == far or near not in expiries or far not in expiries or near > far:
            return [], ["日历价差近月/远月无效"]
        near_calls = [c for c in chain if c["type"] == "call" and c["expiry"] == near]
        far_calls = [c for c in chain if c["type"] == "call" and c["expiry"] == far]
        common = sorted(
            {c["strike"] for c in near_calls} & {c["strike"] for c in far_calls},
            key=lambda strike: abs(strike - spot),
        )
        if not common:
            return [], ["日历价差两个到期月没有共同执行价"]
        strike = common[0]
        near_leg = next(c for c in near_calls if c["strike"] == strike)
        far_leg = next(c for c in far_calls if c["strike"] == strike)
        notes.append(f"日历价差：卖近月认购({near}) + 买远月认购({far}) K={strike}")
        return [_leg(near_leg, "short"), _leg(far_leg, "long")], notes

    selected_expiry = expiry or (expiries[0] if expiries else None)
    chain = [c for c in chain if c["expiry"] == selected_expiry]
    calls = [c for c in chain if c["type"] == "call"]
    puts = [c for c in chain if c["type"] == "put"]
    if not chain:
        return [], [f"到期月 {selected_expiry} 无可用合约"]

    if strategy_type == "vertical_spread":
        if view == "bearish":
            if len(puts) < 2:
                notes.append("认沽合约不足 2 档，无法构建熊市价差")
                return [], notes
            # 熊市价差：买高行权价 put + 卖低行权价 put（借记）。选现价附近相邻两档，保证 strike 不同。
            lo, hi = _spread_pair(puts, spot, bull=False)
            notes.append(f"熊市价差：买认沽 K={hi['strike']} + 卖认沽 K={lo['strike']}（借记，两腿行权价不同）")
            return [_leg(hi, "long"), _leg(lo, "short")], notes
        else:
            if len(calls) < 2:
                notes.append("认购合约不足 2 档，无法构建牛市价差")
                return [], notes
            # 牛市价差：买低行权价 call + 卖高行权价 call（借记）。选现价附近相邻两档，保证 strike 不同。
            lo, hi = _spread_pair(calls, spot, bull=True)
            notes.append(f"牛市价差：买认购 K={lo['strike']} + 卖认购 K={hi['strike']}（借记，两腿行权价不同）")
            return [_leg(lo, "long"), _leg(hi, "short")], notes

    if strategy_type == "straddle":
        if not calls or not puts:
            return [], ["跨式需要同到期月认购与认沽"]
        atm_c, atm_p = _atm(calls, spot), _atm(puts, spot)
        notes.append(f"跨式：买 ATM 认购 + 买 ATM 认沽 K≈{atm_c['strike']}（做多波动）")
        return [_leg(atm_c, "long"), _leg(atm_p, "long")], notes

    if strategy_type == "strangle":
        if not calls or not puts:
            return [], ["宽跨式需要同到期月认购与认沽"]
        oc, op = _otm_call(calls, spot), _otm_put(puts, spot)
        notes.append(f"宽跨式：买 OTM 认购 K={oc['strike']} + 买 OTM 认沽 K={op['strike']}（做多波动，成本更低）")
        return [_leg(oc, "long"), _leg(op, "long")], notes

    if strategy_type == "collar":
        if not calls or not puts:
            return [], ["领口需要同到期月认购与认沽"]
        oc, op = _otm_call(calls, spot), _otm_put(puts, spot)
        notes.append(f"领口：持标的 + 买 OTM 认沽 K={op['strike']}（保护）+ 卖 OTM 认购 K={oc['strike']}（降本）")
        return [
            underlying_leg(spot, oc["contract_size"], selected_expiry),
            _leg(op, "long"),
            _leg(oc, "short"),
        ], notes

    if strategy_type == "covered_call":
        if not calls:
            return [], ["备兑看涨需要认购合约"]
        oc = _otm_call(calls, spot)
        notes.append(f"备兑看涨：持标的 + 卖 OTM 认购 K={oc['strike']}（增强收益，放弃上行）")
        return [
            underlying_leg(spot, oc["contract_size"], selected_expiry),
            _leg(oc, "short"),
        ], notes

    return [], [f"未知结构 {strategy_type}，未生成策略腿"]
