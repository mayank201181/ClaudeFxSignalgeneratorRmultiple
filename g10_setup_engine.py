"""
g10_setup_engine.py
====================
Asymmetric R-multiple trade-setup engine for G10 FX (daily data).

Design principles (the parts that matter more than the indicators):
  * Everything is normalised to R = abs(entry - stop). A trade that risks 40 pips
    and makes 160 is +4R; a stop-out is -1R. This makes every pair comparable and
    lets us POOL signals across the whole complex to fix the small-sample problem.
  * The backtest is BARRIER-based, not "20-day forward return": for each signal we
    ask "did price reach +kR before -1R?" - which is how the book is actually run.
  * Daily OHLC can't tell us whether the stop or the target was touched first inside
    a bar. We use the CONSERVATIVE convention (stop assumed first when both are in
    range) so EV is understated, not flattered. Calibrate the haircut later with tick.
  * We report MAE / MFE so stop and target placement is driven by evidence, not feel.
  * Robustness > optimisation: sweep parameters, split by decade, never trust a curve
    that lives or dies on three trades.

Swap `load_data_yf` for a Bloomberg loader at the office; everything downstream is
data-source agnostic (it just needs OHLC columns: Open/High/Low/Close).

Author note: starter engine - extend the SIGNALS dict and the metrics as needed.
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass, field

# ----------------------------------------------------------------------------- #
# 0. CONFIG
# ----------------------------------------------------------------------------- #

# yfinance FX tickers. Dollar pairs + major crosses. (Yahoo history ~2003+.)
UNIVERSE = {
    # USD pairs
    "EURUSD": "EURUSD=X", "USDJPY": "USDJPY=X", "GBPUSD": "GBPUSD=X",
    "AUDUSD": "AUDUSD=X", "NZDUSD": "NZDUSD=X", "USDCAD": "USDCAD=X",
    "USDCHF": "USDCHF=X", "USDNOK": "USDNOK=X", "USDSEK": "USDSEK=X",
    # Crosses (often the cleaner technical signal than forcing a USD leg)
    "EURJPY": "EURJPY=X", "GBPJPY": "GBPJPY=X", "AUDJPY": "AUDJPY=X",
    "CADJPY": "CADJPY=X", "EURAUD": "EURAUD=X", "EURGBP": "EURGBP=X",
    "AUDNZD": "AUDNZD=X", "GBPAUD": "GBPAUD=X",
}

TARGET_MULTS = [1, 2, 3, 5]   # R-multiple barriers to test
MAX_HORIZON  = 40             # trading days a setup is given to work (time stop)


# ----------------------------------------------------------------------------- #
# 1. DATA LAYER
# ----------------------------------------------------------------------------- #

def load_data_yf(universe: dict = UNIVERSE, start: str = "2003-01-01") -> dict:
    """Pull daily OHLC from Yahoo. Returns {name: DataFrame[Open,High,Low,Close]}."""
    import yfinance as yf
    out = {}
    for name, tkr in universe.items():
        df = yf.download(tkr, start=start, auto_adjust=False, progress=False)
        if df.empty:
            print(f"  ! no data for {name} ({tkr})")
            continue
        if isinstance(df.columns, pd.MultiIndex):       # yf sometimes returns MultiIndex
            df.columns = df.columns.get_level_values(0)
        df = df[["Open", "High", "Low", "Close"]].dropna()
        out[name] = df
        print(f"  {name}: {len(df)} bars  {df.index[0].date()} -> {df.index[-1].date()}")
    return out


# ----------------------------------------------------------------------------- #
# 2. FEATURE LAYER  (all point-in-time, no look-ahead)
# ----------------------------------------------------------------------------- #

def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c, h, l = d["Close"], d["High"], d["Low"]

    d["ret"] = np.log(c / c.shift(1))

    # True range / ATR (simple mean; switch to Wilder EMA if preferred)
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean()

    # Realised vol + its 3y percentile (compression detector)
    d["rvol"]    = d["ret"].rolling(20).std() * np.sqrt(252)
    d["rvol_pct"] = d["rvol"].rolling(756, min_periods=252).rank(pct=True)

    # Moving averages + slopes
    for n in (20, 50, 100, 200):
        d[f"ma{n}"] = c.rolling(n).mean()
    d["ma50_slope"]  = d["ma50"].diff(5)
    d["ma100_slope"] = d["ma100"].diff(5)

    # Donchian channels (prior N-day extreme, shifted so today isn't included)
    d["dc_hi"] = h.rolling(60).max().shift(1)
    d["dc_lo"] = l.rolling(60).min().shift(1)

    # RSI(14)
    delta = c.diff()
    up = delta.clip(lower=0).rolling(14).mean()
    dn = (-delta.clip(upper=0)).rolling(14).mean()
    d["rsi"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))

    # Distance from 50d MA in ATR units (stretch)
    d["stretch_atr"] = (c - d["ma50"]) / d["atr"]

    # Coarse regime label (used to slice results, not to fire signals)
    trend_up   = (d["ma20"] > d["ma50"]) & (d["ma50"] > d["ma100"]) & (d["ma50_slope"] > 0)
    trend_dn   = (d["ma20"] < d["ma50"]) & (d["ma50"] < d["ma100"]) & (d["ma50_slope"] < 0)
    d["regime"] = np.where(trend_up, "trend_up",
                  np.where(trend_dn, "trend_dn",
                  np.where(d["rvol_pct"] < 0.25, "compressed", "range")))
    return d


# ----------------------------------------------------------------------------- #
# 3. SIGNAL LAYER
# Each signal -> DataFrame of events: [date, direction, entry_ref, stop_ref]
# entry_ref/stop_ref are reference prices off the SIGNAL bar; real entry is next
# bar's open (handled in the backtester) to avoid same-bar look-ahead.
# ----------------------------------------------------------------------------- #

def sig_vol_compression_breakout(d: pd.DataFrame, atr_stop=1.0, vol_pct_max=0.25):
    comp = d["rvol_pct"] < vol_pct_max
    long_  = comp & (d["Close"] > d["dc_hi"])
    short_ = comp & (d["Close"] < d["dc_lo"])
    ev = []
    for dt in d.index[long_.fillna(False)]:
        row = d.loc[dt]
        ev.append((dt, "long", row["Close"], row["Close"] - atr_stop * row["atr"]))
    for dt in d.index[short_.fillna(False)]:
        row = d.loc[dt]
        ev.append((dt, "short", row["Close"], row["Close"] + atr_stop * row["atr"]))
    return _events_df(ev, "vol_compression_breakout")


def sig_trend_pullback(d: pd.DataFrame, look=5):
    out = []
    bull = (d["ma20"] > d["ma50"]) & (d["ma50"] > d["ma100"]) & (d["ma50_slope"] > 0)
    bear = (d["ma20"] < d["ma50"]) & (d["ma50"] < d["ma100"]) & (d["ma50_slope"] < 0)
    hi3 = d["High"].rolling(3).max().shift(1)
    lo3 = d["Low"].rolling(3).min().shift(1)
    touched_ma_dn = (d["Low"] <= d["ma20"]).rolling(look).max().astype(bool)   # pulled back to MA recently
    touched_ma_up = (d["High"] >= d["ma20"]).rolling(look).max().astype(bool)
    long_  = bull & touched_ma_dn & (d["Close"] > hi3)
    short_ = bear & touched_ma_up & (d["Close"] < lo3)
    for dt in d.index[long_.fillna(False)]:
        row = d.loc[dt]
        stop = d["Low"].loc[:dt].iloc[-look:].min()              # below the pullback low
        if stop < row["Close"]:
            out.append((dt, "long", row["Close"], stop))
    for dt in d.index[short_.fillna(False)]:
        row = d.loc[dt]
        stop = d["High"].loc[:dt].iloc[-look:].max()
        if stop > row["Close"]:
            out.append((dt, "short", row["Close"], stop))
    return _events_df(out, "trend_pullback")


def sig_failed_breakout(d: pd.DataFrame, window=3, buf_atr=0.1):
    out = []
    broke_hi = (d["High"] > d["dc_hi"]).rolling(window).max().astype(bool)     # broke out in last N bars
    broke_lo = (d["Low"]  < d["dc_lo"]).rolling(window).max().astype(bool)
    back_in_short = broke_hi & (d["Close"] < d["dc_hi"])                       # closed back inside -> fade
    back_in_long  = broke_lo & (d["Close"] > d["dc_lo"])
    for dt in d.index[back_in_short.fillna(False)]:
        row = d.loc[dt]
        extreme = d["High"].loc[:dt].iloc[-window:].max()
        stop = extreme + buf_atr * row["atr"]
        if stop > row["Close"]:
            out.append((dt, "short", row["Close"], stop))
    for dt in d.index[back_in_long.fillna(False)]:
        row = d.loc[dt]
        extreme = d["Low"].loc[:dt].iloc[-window:].min()
        stop = extreme - buf_atr * row["atr"]
        if stop < row["Close"]:
            out.append((dt, "long", row["Close"], stop))
    return _events_df(out, "failed_breakout")


SIGNALS = {
    "vol_compression_breakout": sig_vol_compression_breakout,
    "trend_pullback":           sig_trend_pullback,
    "failed_breakout":          sig_failed_breakout,
}


def _events_df(rows, name):
    df = pd.DataFrame(rows, columns=["date", "direction", "entry_ref", "stop_ref"])
    df["signal"] = name
    return df.sort_values("date").reset_index(drop=True)


# ----------------------------------------------------------------------------- #
# 4. BARRIER BACKTEST  (the heart of it)
# ----------------------------------------------------------------------------- #

@dataclass
class TradeResult:
    pair: str; signal: str; date: pd.Timestamp; direction: str
    R_price: float; regime: str
    target_day: dict = field(default_factory=dict)   # k -> bar index of first touch (pre-stop)
    stop_day: int | None = None
    mae_R: float = 0.0; mfe_R: float = 0.0
    terminal_R: float = 0.0; held: int = 0


def _evaluate(future: pd.DataFrame, direction: str, entry: float, stop: float,
              max_days: int, conservative=True) -> dict:
    """Walk one trade forward over OHLC. Returns barrier touches, MAE/MFE, terminal R."""
    R = abs(entry - stop)
    res = dict(target_day={}, stop_day=None, mae_R=0.0, mfe_R=0.0, terminal_R=0.0, held=0)
    if R == 0 or future.empty:
        return res
    if direction == "long":
        stop_lvl = entry - R
        tgt = {k: entry + k * R for k in TARGET_MULTS}
    else:
        stop_lvl = entry + R
        tgt = {k: entry - k * R for k in TARGET_MULTS}

    for i in range(min(max_days, len(future))):
        hi, lo, cl = future["High"].iloc[i], future["Low"].iloc[i], future["Close"].iloc[i]
        # excursions in R
        if direction == "long":
            res["mfe_R"] = max(res["mfe_R"], (hi - entry) / R)
            res["mae_R"] = max(res["mae_R"], (entry - lo) / R)
            stop_touch = lo <= stop_lvl
            tgt_touch  = {k: hi >= tgt[k] for k in TARGET_MULTS}
        else:
            res["mfe_R"] = max(res["mfe_R"], (entry - lo) / R)
            res["mae_R"] = max(res["mae_R"], (hi - entry) / R)
            stop_touch = hi >= stop_lvl
            tgt_touch  = {k: lo <= tgt[k] for k in TARGET_MULTS}

        res["held"] = i + 1
        # CONSERVATIVE: stop wins same-bar ties. Stop closes the trade.
        if stop_touch and conservative:
            res["stop_day"] = i + 1
            res["terminal_R"] = -1.0
            return res
        for k in TARGET_MULTS:                       # record first touch (pre-stop)
            if tgt_touch[k] and k not in res["target_day"]:
                res["target_day"][k] = i + 1
        if stop_touch and not conservative and not any(tgt_touch.values()):
            res["stop_day"] = i + 1
            res["terminal_R"] = -1.0
            return res

    # neither barrier ended it: mark to market at horizon close
    last_c = future["Close"].iloc[min(max_days, len(future)) - 1]
    res["terminal_R"] = ((last_c - entry) if direction == "long" else (entry - last_c)) / R
    return res


def backtest(data_feat: dict, signals=SIGNALS, max_days=MAX_HORIZON, conservative=True):
    """Run every signal over every pair. Entry = next bar's open. Returns DataFrame of trades."""
    trades = []
    for pair, d in data_feat.items():
        for name, fn in signals.items():
            ev = fn(d)
            for _, e in ev.iterrows():
                loc = d.index.get_loc(e["date"])
                if loc + 1 >= len(d):                 # need a next bar to enter on
                    continue
                entry = d["Open"].iloc[loc + 1]        # realistic, no same-bar look-ahead
                # rescale stop distance to the actual entry (ref was the signal close)
                stop = entry - (e["entry_ref"] - e["stop_ref"])
                future = d.iloc[loc + 1: loc + 1 + max_days]
                r = _evaluate(future, e["direction"], entry, stop, max_days, conservative)
                trades.append(dict(
                    pair=pair, signal=name, date=e["date"], direction=e["direction"],
                    R_price=abs(entry - stop), regime=d["regime"].iloc[loc],
                    mae_R=r["mae_R"], mfe_R=r["mfe_R"], terminal_R=r["terminal_R"],
                    held=r["held"], stop_day=r["stop_day"],
                    **{f"tgt{k}_day": r["target_day"].get(k) for k in TARGET_MULTS},
                ))
    return pd.DataFrame(trades)


# ----------------------------------------------------------------------------- #
# 5. METRICS  (outcomes derived under explicit exit rules)
# ----------------------------------------------------------------------------- #

def outcome_R(trades: pd.DataFrame, target=3) -> pd.Series:
    """Realised R under 'exit at +target R, else -1R stop, else mark-to-market at horizon'."""
    hit  = trades[f"tgt{target}_day"].notna()
    stop = trades["stop_day"].notna() & ~hit
    out  = trades["terminal_R"].copy()
    out[hit]  = float(target)
    out[stop] = -1.0
    return out


def _worst_streak(r: pd.Series) -> float:
    cum, worst = 0.0, 0.0
    for x in r:
        cum = min(0.0, cum + x) if x < 0 else 0.0
        worst = min(worst, cum)
    return worst


def summarize(trades: pd.DataFrame, target=3, label="ALL") -> dict:
    if trades.empty:
        return {"label": label, "n": 0}
    r = outcome_R(trades, target=target)
    wins, losses = r[r > 0], r[r <= 0]
    bar = lambda k: ((trades[f"tgt{k}_day"].notna()) &
                     ((trades["stop_day"].isna()) | (trades[f"tgt{k}_day"] < trades["stop_day"]))).mean()
    # holding period under the chosen exit rule: target-day if hit, else stop-day, else horizon
    exit_bar = trades[f"tgt{target}_day"].fillna(trades["stop_day"]).fillna(trades["held"])
    return {
        "label": label, "n": int(len(trades)),
        "EV_R": round(r.mean(), 3), "median_R": round(r.median(), 3),
        "hit_rate": round((r > 0).mean(), 3),
        "avg_win": round(wins.mean(), 2) if len(wins) else np.nan,
        "avg_loss": round(losses.mean(), 2) if len(losses) else np.nan,
        "P(2R<stop)": round(bar(2), 3), "P(3R<stop)": round(bar(3), 3),
        "P(5R<stop)": round(bar(5), 3),
        "avg_hold": round(exit_bar.mean(), 1),
        "MAE_win_p75": round(trades.loc[r > 0, "mae_R"].quantile(0.75), 2) if len(wins) else np.nan,
        "MFE_med": round(trades["mfe_R"].median(), 2),
        "worst_streak_R": round(_worst_streak(r), 1),
    }


def report(trades: pd.DataFrame, target=3):
    rows = [summarize(trades, target, "POOLED (all pairs)")]
    for sig in sorted(trades["signal"].unique()):
        rows.append(summarize(trades[trades.signal == sig], target, f"signal={sig}"))
    for reg in sorted(trades["regime"].unique()):
        rows.append(summarize(trades[trades.regime == reg], target, f"regime={reg}"))
    rep = pd.DataFrame(rows).set_index("label")
    return rep


# ----------------------------------------------------------------------------- #
# 6. LIVE OUTPUT  (today's blotter)
# ----------------------------------------------------------------------------- #

def live_blotter(data_feat: dict, stats: pd.DataFrame, signals=SIGNALS, target=3):
    """Signals firing on the most recent bar, with entry/stop/targets + pooled EV stats."""
    rows = []
    for pair, d in data_feat.items():
        last = d.index[-1]
        for name, fn in signals.items():
            ev = fn(d)
            today = ev[ev["date"] == last]
            for _, e in today.iterrows():
                entry, stop = e["entry_ref"], e["stop_ref"]
                R = abs(entry - stop)
                tgts = {f"{k}R": round(entry + (k * R if e.direction == "long" else -k * R), 5)
                        for k in TARGET_MULTS}
                s = stats.loc[f"signal={name}"] if f"signal={name}" in stats.index else {}
                rows.append(dict(pair=pair, dir=e.direction, signal=name,
                                 entry=round(entry, 5), stop=round(stop, 5), R=round(R, 5),
                                 **tgts, EV_R=s.get("EV_R"), hit=s.get("hit_rate"),
                                 P3R=s.get("P(3R<stop)"), regime=d["regime"].iloc[-1]))
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- #
# 7. ROBUSTNESS HELPERS
# ----------------------------------------------------------------------------- #

def decade_split(trades: pd.DataFrame, target=3):
    t = trades.copy(); t["yr"] = pd.to_datetime(t["date"]).dt.year
    buckets = {"2003-2010": (2003, 2010), "2011-2017": (2011, 2017), "2018-2026": (2018, 2026)}
    return pd.DataFrame([summarize(t[(t.yr >= a) & (t.yr <= b)], target, k)
                         for k, (a, b) in buckets.items()]).set_index("label")


def param_sweep(data_feat, atr_stops=(0.75, 1.0, 1.25, 1.5), vol_caps=(0.20, 0.25, 0.30), target=3):
    rows = []
    for a in atr_stops:
        for v in vol_caps:
            sig = {"vcb": lambda d, a=a, v=v: sig_vol_compression_breakout(d, atr_stop=a, vol_pct_max=v)}
            tr = backtest(data_feat, signals=sig)
            s = summarize(tr, target, f"atr_stop={a}, vol_cap={v}")
            rows.append(s)
    return pd.DataFrame(rows).set_index("label")


# ----------------------------------------------------------------------------- #
# 8. PIPELINE
# ----------------------------------------------------------------------------- #

def run(start="2003-01-01", target=3):
    print("Loading data ...")
    raw = load_data_yf(start=start)
    feat = {k: compute_features(v) for k, v in raw.items()}
    print("\nBacktesting (barrier, conservative path) ...")
    trades = backtest(feat)
    print(f"  {len(trades)} historical setups across {trades['pair'].nunique()} pairs\n")
    print("=== POOLED / BY SIGNAL / BY REGIME (exit at +%dR) ===" % target)
    print(report(trades, target).to_string())
    print("\n=== DECADE STABILITY ===")
    print(decade_split(trades, target).to_string())
    print("\n=== TODAY'S BLOTTER ===")
    bl = live_blotter(feat, report(trades, target), target=target)
    print(bl.to_string() if not bl.empty else "  (no signals firing on latest bar)")
    return feat, trades


if __name__ == "__main__":
    run()
