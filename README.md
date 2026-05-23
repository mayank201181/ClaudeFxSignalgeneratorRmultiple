# ClaudeFxSignalgeneratorRmultiple

Asymmetric R-multiple trade-setup engine for G10 FX (daily data).

## Run

```bash
pip install yfinance pandas numpy
python g10_setup_engine.py                       # default: Yahoo Finance
python g10_setup_engine.py --source synthetic    # offline GBM data (no network)
python g10_setup_engine.py --start 2010-01-01 --target 3
```

## Network note

`--source yf` pulls daily OHLC from Yahoo Finance via `yfinance`, which hits
`query1.finance.yahoo.com`. In environments with restrictive network policies
(e.g. Claude Code's "Trusted hosts" sandbox) this host may be blocked and you'll
see `HTTP 403: Host not in allowlist`. Either:

- Run locally where Yahoo is reachable, or
- Switch the environment's network policy to a less restrictive one (see
  https://code.claude.com/docs/en/claude-code-on-the-web), or
- Use `--source synthetic` to exercise the pipeline end-to-end with generated
  data (useful for development / smoke testing — not a real backtest).

## Output

Three blocks per run:

1. **POOLED / BY SIGNAL / BY REGIME** — full-history stats under
   "exit at +`target` R, else -1R stop, else mark-to-market at horizon".
2. **DECADE STABILITY** — same stats split into 2003-2010 / 2011-2017 / 2018-2026.
3. **TODAY'S BLOTTER** — any signals firing on the most recent bar, with
   entry / stop / target levels and the pooled EV stats for that signal type.
