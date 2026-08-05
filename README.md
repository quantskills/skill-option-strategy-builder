# skill-option-strategy-builder

[English](README.en.md)

期权策略构建器 —— 输入标的（ETF/指数期权，如 510050.SH 50ETF期权）、结构类型与方向观点，自动**选腿、定价、算希腊、画损益**，产出一张可计算的**策略卡**：损益图、盈亏平衡、最大盈亏、净希腊字母、保证金占用。

> QuantSkills 组织技能 · 数据源 Pandadata · 组织首个期权**策略构建**工具（与 `skill-options-vol-analyst` 的 IV/skew **分析**互补）· 仅供研究参考，不构成投资建议。

## 快速开始

```bash
pip install -r requirements.txt   # 纯标准库，无强制依赖（BS 用 math.erf，不需 numpy/scipy）

# 离线演示（无需凭证，内置样本 → 牛市价差策略卡）
python examples/run_demo.py

# 真实构建（需 panda_data SDK，见 skill-pandadata-api）
python scripts/strategy_card.py --underlying 510050.SH --type vertical_spread \
    --view bullish --contracts 1 --as-of 20260728 --expiry 20260826 \
    --out card.json --md card.md
```

## 支持的结构（7 种）

| 结构 | 说明 | 观点驱动选腿 |
|---|---|---|
| `vertical_spread` 垂直价差 | 一买一卖，控成本 | 牛市=call 价差 / 熊市=put 价差 |
| `straddle` 跨式 | 买 ATM 认购+认沽 | 做多波动 |
| `strangle` 宽跨式 | 买 OTM 认购+认沽 | 做多波动（低成本） |
| `collar` 领口 | 持标的+买 OTM 认沽+卖 OTM 认购 | 保护性区间 |
| `calendar` 日历价差 | 卖近月+买远月 ATM | 必须有两个到期月；近月到期截面重估远月时间价值 |
| `covered_call` 备兑看涨 | 持标的+卖 OTM 认购 | 温和看涨/震荡增强 |
| `custom` 自定义腿 | 手动指定行权价+方向+数量 | — |

选腿规则见 `references/strategies.md`。

## 数据源三层回退

`scripts/data_source.py` 统一处理 SDK 四种返回形态（DataFrame / list[dict] / `{result:[]}` / gateway dataframe `{type,columns,rows}`）：

1. **生产**：`import panda_data` SDK 直连。
2. **无 SDK**：回退 `examples/sample_data/*.json` 内置样本，离线可跑。
3. `--prefer sample` 强制样本。

## 希腊字母：接口优先，缺失 BS 补算

- 优先用 `get_option_risk_indicators` 的真实 `delta/gamma/vega/theta/rho`。
- **实测坑（2026-07-27 端到端验证）**：该接口当日全市场 5 万+条记录**全部是商品期货期权**（DCE/SHF/CZC/GFE/CFE/INE），且 **gamma/vega/theta/rho 100% 为 None，只有 delta 有值**；**SSE/SZSE 的 ETF/指数期权（50ETF 等）在此接口当日 0 条**。
- 换言之，真实数据下：ETF 期权有合约要素（`get_option_static` 的 strike/margin）但**无任何希腊字母**；商品期权有 delta + 日线但 `get_option_static` 返回空。→ **两类期权的完整希腊字母（尤其 gamma/vega/theta）几乎都要靠 BS 补算**。
- 缺失字段用 **BS 模型（`math.erf` 实现正态CDF，纯标准库）** 补算并在 `degraded` 声明。详见 `references/greeks.md`。
- IV 按已选策略腿读取并统一为小数；接口 IV 缺失时用该腿真实收盘价反解。没有真实价格或反解失败时卡片 `status=failed`，不再用固定 `IV=0.20`。

## 接口字段（2026-07-27 实测确认）

| 接口 | 关键字段 |
|---|---|
| `get_option_static` | strike_price, call_put_code(CO认购/PO认沽), delisted_date(到期日), contract_size(合约单位), exercise_style(E欧式), **margin(单位保证金,直接给)**, underlying_pre_close(标的昨收), open_interest, pre_close。入参 start_date/end_date 必填 |
| `get_option_daily` | date, symbol, close, settlement, volume, open_interest |
| `get_option_risk_indicators` | symbol, name, exchange, date, delta, gamma, vega, theta, rho |
| `get_option_implied_volatility` | date, symbol, implied_volatility |

日期统一 YYYYMMDD；symbol 传 `""` 全市场。

## 输出契约 StrategyCard

`status / requested_date / data_date / valuation_date / legs[] / net_premium / breakevens[] / max_profit / max_loss / net_greeks{delta,gamma,vega,theta,rho} / margin_est / payoff_curve[] / sources / degraded[] / errors[]`

领口和备兑卡包含标的腿；custom 必须用 `--legs-json` 提供腿。缺策略腿、关键行情或可靠 IV 时 CLI 返回非零退出码。

## 目录

```
scripts/     data_source.py · legs.py · pricing.py · payoff.py · strategy_card.py · formatters.py
references/  strategies.md · greeks.md
examples/    run_demo.py · sample_data/ · sample_report.md
```

## 免责

希腊字母缺失以 BS 模型补算（近似），保证金为规则估算，实际以券商/交易所为准。数据非实时。仅供研究参考，不构成投资建议。

## 许可证

GPL-3.0-only，详见 [LICENSE](LICENSE)。
