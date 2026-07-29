"""formatters.py — StrategyCard 渲染 JSON / 中文文本 / ASCII 损益图 / Markdown"""
from __future__ import annotations
import json

DISCLAIMER = "仅供研究参考，不构成投资建议。"


def to_json(card: dict) -> str:
    return json.dumps(card, ensure_ascii=False, indent=2)


def _fmt_money(v):
    if v is None:
        return "—"
    return f"{v:+,.0f}元"


def ascii_payoff(curve, breakevens, width=52, height=15) -> str:
    """
    ASCII 到期损益图：横轴标的价、纵轴损益，零轴用 '─'，盈亏拐点清晰可见。
    """
    if not curve:
        return "(无损益曲线)"
    xs = [p["S"] for p in curve]
    ys = [p["payoff"] for p in curve]
    ymin, ymax = min(ys), max(ys)
    if ymax == ymin:
        ymax += 1.0
    # 采样到 width 列
    n = len(curve)
    cols = []
    for i in range(width):
        idx = round(i * (n - 1) / (width - 1))
        cols.append(curve[idx])

    def row_for(y):
        # 把 y 映射到 [0, height-1]，越大越靠上
        frac = (y - ymin) / (ymax - ymin)
        return int(round((height - 1) * (1 - frac)))

    zero_row = row_for(0.0) if ymin <= 0 <= ymax else None
    grid = [[" "] * width for _ in range(height)]
    if zero_row is not None:
        for c in range(width):
            grid[zero_row][c] = "─"
    for c, p in enumerate(cols):
        r = row_for(p["payoff"])
        grid[r][c] = "█" if p["payoff"] >= 0 else "▒"

    lines = []
    for r in range(height):
        if r == 0:
            label = f"{ymax:>+10,.0f}"
        elif r == height - 1:
            label = f"{ymin:>+10,.0f}"
        elif zero_row is not None and r == zero_row:
            label = f"{0:>+10,.0f}"
        else:
            label = " " * 10
        lines.append(f"{label} │" + "".join(grid[r]))
    # 横轴价位刻度
    axis = " " * 11 + "└" + "─" * width
    lo, hi = xs[0], xs[-1]
    mid = (lo + hi) / 2
    ticks = f"{'':11} {lo:<.3f}{' ' * (width // 2 - 8)}{mid:.3f}{' ' * (width // 2 - 8)}{hi:.3f}"
    lines.append(axis)
    lines.append(ticks)
    if breakevens:
        lines.append(" " * 11 + "盈亏平衡点: " + " , ".join(f"{b:.4f}" for b in breakevens))
    lines.append(" " * 11 + "█=盈利  ▒=亏损  ─=零轴")
    return "\n".join(lines)


def _side_cn(side):
    return "买" if side == "long" else "卖"


def _type_cn(t):
    return {"call": "认购", "put": "认沽", "underlying": "标的"}.get(t, t)


def to_text(card: dict) -> str:
    L = []
    L.append("=" * 60)
    L.append(f"期权策略卡 · {card['strategy_type_cn']}（{card['view_cn']}）")
    L.append(f"标的: {card['underlying']}   合约数: {card['contracts']} 张   "
             f"生成: {card['generated_at']}   后端: {card['backend']}")
    L.append(f"状态: {card.get('status')}   数据日: {card.get('data_date')}   "
             f"估值日: {card.get('valuation_date')}")
    L.append(f"标的现价: {card['spot']}   估算IV: {card.get('sigma_used')}")
    L.append("=" * 60)

    L.append("【策略腿】")
    for lg in card["legs"]:
        m = lg.get("margin")
        if lg["type"] == "underlying":
            L.append(f"  买 标的  建仓价 {lg['entry_price']}  数量 {lg['qty']}  "
                     f"合约单位 {lg.get('contract_size')}")
        else:
            L.append(f"  {_side_cn(lg['side'])} {_type_cn(lg['type'])}  行权价 {lg['strike']}  "
                     f"数量 {lg['qty']}  到期 {lg['expiry']}  "
                     f"权利金 {lg['premium']:.4f}  IV {lg.get('iv')}  "
                     f"希腊来源[{lg['greeks_source']}]"
                     + (f"  单位保证金 {m}" if m else ""))

    L.append("")
    L.append("【关键指标】")
    np_cf = card["net_premium"]
    tag = "净收权利金(贷记)" if np_cf > 0 else "净付权利金(借记)"
    L.append(f"  净权利金现金流: {_fmt_money(np_cf)}（{tag}）")
    mp = card["max_profit"]; ml = card["max_loss"]
    mp_s = "理论上无上限" if card.get("max_profit_unbounded") else _fmt_money(mp)
    ml_s = "理论上无下限" if card.get("max_loss_unbounded") else _fmt_money(ml)
    metric_prefix = "模型区间" if card.get("model_range") else ""
    L.append(f"  {metric_prefix}最大盈利: {mp_s}")
    L.append(f"  {metric_prefix}最大亏损: {ml_s}")
    if card["breakevens"]:
        L.append(f"  盈亏平衡点: " + " , ".join(f"{b:.4f}" for b in card["breakevens"]))
    else:
        L.append("  盈亏平衡点: （样本区间内未出现过零点）")

    L.append("")
    L.append("【净希腊字母】(整个组合，已乘合约单位与合约数)")
    g = card["net_greeks"]
    L.append(f"  delta {g['delta']:+.2f}   gamma {g['gamma']:+.4f}   "
             f"vega {g['vega']:+.2f}   theta {g['theta']:+.2f}   rho {g['rho']:+.2f}")
    L.append(f"  · delta {g['delta']:+.2f}: 标的每涨 1 元，组合价值约变 {g['delta']:+.2f} 元")
    L.append(f"  · theta {g['theta']:+.2f}: 每年时间价值损益（负=持仓每日损耗）")
    L.append(f"  · vega  {g['vega']:+.2f}: IV 变动 100% 的影响（IV +1% ≈ {g['vega']/100:+.2f} 元）")

    L.append("")
    L.append(f"【保证金占用】约 {_fmt_money(card['margin_est']['margin_total'])}")
    for md in card["margin_est"]["legs"]:
        L.append(f"    卖腿 K={md['strike']}: {_fmt_money(md['margin'])}  来源[{md['source']}]")

    L.append("")
    L.append("【到期损益图】")
    L.append(ascii_payoff(card["payoff_curve"], card["breakevens"]))

    if card.get("degraded"):
        L.append("")
        L.append("⚠️ 数据降级 / 说明:")
        for d in card["degraded"]:
            L.append(f"    - {d}")
    if card.get("errors"):
        L.append("")
        L.append("❌ 失败原因:")
        for error in card["errors"]:
            L.append(f"    - {error}")

    L.append("")
    L.append(f"免责声明：希腊字母缺失时以 BS 模型补算，保证金为估算。{DISCLAIMER}")
    return "\n".join(L)


def to_markdown(card: dict) -> str:
    L = [f"# 期权策略卡 — {card['strategy_type_cn']}（{card['view_cn']}）", ""]
    L.append(f"标的 `{card['underlying']}` · 合约 {card['contracts']} 张 · 后端 `{card['backend']}` · {card['generated_at']}")
    L.append("")
    L.append("## 策略腿")
    L.append("| 方向 | 类型 | 行权价 | 数量 | 到期 | 权利金 | 希腊来源 |")
    L.append("|---|---|---|---|---|---|---|")
    for lg in card["legs"]:
        strike = lg["entry_price"] if lg["type"] == "underlying" else lg["strike"]
        L.append(f"| {_side_cn(lg['side'])} | {_type_cn(lg['type'])} | {strike} | "
                 f"{lg['qty']} | {lg['expiry']} | {lg['premium']:.4f} | {lg['greeks_source']} |")
    L.append("")
    mp = "无上限" if card.get("max_profit_unbounded") else _fmt_money(card["max_profit"])
    ml = "无下限" if card.get("max_loss_unbounded") else _fmt_money(card["max_loss"])
    L.append("## 关键指标")
    L.append(f"- 净权利金现金流: **{card['net_premium']:+,.0f}元**")
    metric_prefix = "模型区间" if card.get("model_range") else ""
    L.append(f"- {metric_prefix}最大盈利: **{mp}** ｜ {metric_prefix}最大亏损: **{ml}**")
    L.append(f"- 盈亏平衡点: {' , '.join(f'{b:.4f}' for b in card['breakevens']) or '—'}")
    g = card["net_greeks"]
    L.append(f"- 净希腊: delta {g['delta']:+.2f} · gamma {g['gamma']:+.4f} · "
             f"vega {g['vega']:+.2f} · theta {g['theta']:+.2f} · rho {g['rho']:+.2f}")
    L.append(f"- 保证金占用(估): {card['margin_est']['margin_total']:+,.0f}元")
    L.append("")
    L.append("## 到期损益图")
    L.append("```")
    L.append(ascii_payoff(card["payoff_curve"], card["breakevens"]))
    L.append("```")
    if card.get("degraded"):
        L.append("")
        L.append("## 数据降级")
        for d in card["degraded"]:
            L.append(f"- {d}")
    if card.get("errors"):
        L.append("")
        L.append("## 失败原因")
        for error in card["errors"]:
            L.append(f"- {error}")
    L.append("")
    L.append(f"> 希腊字母缺失以 BS 补算，保证金为估算。{DISCLAIMER}")
    return "\n".join(L)
