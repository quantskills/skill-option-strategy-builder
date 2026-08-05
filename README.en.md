# Option Strategy Builder

[Chinese README](README.md) | English

This skill builds analyzable strategies for A-share ETF and index options. Given an
underlying, view, expiry, and structure, it selects legs, prices them, calculates the
payoff curve and breakevens, and reports net Greeks and estimated margin.

## Quick start

```bash
pip install -r requirements.txt
python examples/run_demo.py
python scripts/strategy_card.py --underlying 510050.SH --type vertical_spread \
  --view bullish --contracts 1 --as-of 20260728 --expiry 20260826 \
  --out card.json --md card.md
```

Supported structures include vertical spreads, straddles, strangles, collars,
calendars, covered calls, and custom legs. The report is a `StrategyCard` with legs,
premium, breakevens, payoff samples, Greeks, margin, sources, and errors/degradation.

## Data and controls

The data adapter can call `get_option_static`, `get_option_daily`,
`get_option_risk_indicators`, and `get_option_implied_volatility`. Missing Greeks may be
estimated with Black-Scholes and are marked as approximations. Missing critical legs or
market data causes a non-zero result instead of a partial signal.

## Research boundary

Greeks, margin, and payoff values depend on assumptions and are not real-time quotes.
Outputs are for research and education only; this project does not place orders and is
not investment advice. Confirm contract rules and costs with the applicable venue.

## License

GPL-3.0-only. See [LICENSE](LICENSE).
