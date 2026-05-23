"""Streamlit UI for the G10 FX asymmetric R-multiple setup engine."""

from __future__ import annotations
import io
from contextlib import redirect_stdout

import pandas as pd
import streamlit as st

from g10_setup_engine import (
    UNIVERSE,
    TARGET_MULTS,
    backtest,
    compute_features,
    decade_split,
    live_blotter,
    load_data_yf,
    report,
)

st.set_page_config(page_title="G10 FX Setup Engine", layout="wide")

st.title("G10 FX Asymmetric R-Multiple Setup Engine")
st.caption(
    "Barrier-based backtest on daily Yahoo Finance OHLC. "
    "Every trade is normalised to R = |entry - stop| so signals pool across the complex. "
    "Conservative tie-breaking: stop wins same-bar against target."
)

with st.sidebar:
    st.header("Run parameters")
    selected = st.multiselect(
        "Universe", list(UNIVERSE.keys()), default=list(UNIVERSE.keys()),
        help="Pick which pairs to include. Fewer pairs = faster.",
    )
    start = st.date_input(
        "Start date", value=pd.Timestamp("2010-01-01"),
        min_value=pd.Timestamp("2003-01-01"), max_value=pd.Timestamp.today(),
    )
    target = st.selectbox("Target R-multiple", TARGET_MULTS, index=TARGET_MULTS.index(3))
    run_btn = st.button("Run engine", type="primary", use_container_width=True)
    st.divider()
    st.caption(
        "Data: Yahoo Finance via `yfinance`. First run downloads history; results "
        "are cached for 1 hour."
    )


@st.cache_data(show_spinner=False, ttl=3600)
def _load(universe_keys: tuple, start_str: str) -> dict:
    sub = {k: UNIVERSE[k] for k in universe_keys}
    buf = io.StringIO()
    with redirect_stdout(buf):
        raw = load_data_yf(universe=sub, start=start_str)
    return raw


@st.cache_data(show_spinner=False, ttl=3600)
def _features(raw_keys: tuple, start_str: str) -> dict:
    raw = _load(raw_keys, start_str)
    return {k: compute_features(v) for k, v in raw.items()}


@st.cache_data(show_spinner=False, ttl=3600)
def _backtest(raw_keys: tuple, start_str: str) -> pd.DataFrame:
    feat = _features(raw_keys, start_str)
    return backtest(feat)


def _run():
    if not selected:
        st.warning("Pick at least one pair in the sidebar.")
        return

    keys = tuple(selected)
    start_str = pd.Timestamp(start).strftime("%Y-%m-%d")

    with st.spinner(f"Downloading {len(selected)} pair(s) from Yahoo Finance..."):
        raw = _load(keys, start_str)
    if not raw:
        st.error("Yahoo Finance returned nothing for the selected universe.")
        return

    with st.spinner("Computing features..."):
        feat = _features(keys, start_str)

    with st.spinner("Running barrier backtest..."):
        trades = _backtest(keys, start_str)

    if trades.empty:
        st.warning("No setups fired over this universe/range.")
        return

    st.success(
        f"{len(trades):,} historical setups across {trades['pair'].nunique()} pair(s). "
        f"Date range: {trades['date'].min().date()} - {trades['date'].max().date()}."
    )

    rep = report(trades, target=target)
    blotter = live_blotter(feat, rep, target=target)

    tab_blotter, tab_summary, tab_decades, tab_trades, tab_data = st.tabs(
        ["Today's Blotter", "Summary", "Decade Stability", "All Trades", "Raw Data"]
    )

    with tab_blotter:
        st.subheader(f"Signals firing on the most recent bar (target = +{target}R)")
        if blotter.empty:
            st.info("No signals are firing on the latest bar across the selected universe.")
        else:
            st.dataframe(blotter, use_container_width=True, hide_index=True)

    with tab_summary:
        st.subheader(f"Pooled / by signal / by regime (exit at +{target}R)")
        st.dataframe(rep, use_container_width=True)
        st.caption(
            "EV_R is realised R per trade under: exit at +target R, else -1R stop, "
            "else mark-to-market at horizon. P(kR<stop) is the probability the +kR "
            "barrier is touched before -1R."
        )

    with tab_decades:
        st.subheader(f"Decade stability (target = +{target}R)")
        st.dataframe(decade_split(trades, target=target), use_container_width=True)
        st.caption("Splits trades into buckets to check regime stability.")

    with tab_trades:
        st.subheader("All historical trades")
        pair_filter = st.multiselect("Filter pair", sorted(trades["pair"].unique()))
        sig_filter = st.multiselect("Filter signal", sorted(trades["signal"].unique()))
        view = trades.copy()
        if pair_filter:
            view = view[view["pair"].isin(pair_filter)]
        if sig_filter:
            view = view[view["signal"].isin(sig_filter)]
        st.dataframe(view, use_container_width=True, hide_index=True)
        st.download_button(
            "Download CSV", view.to_csv(index=False).encode(),
            file_name="trades.csv", mime="text/csv",
        )

    with tab_data:
        st.subheader("Per-pair feature data (latest 200 bars)")
        pick = st.selectbox("Pair", sorted(feat.keys()))
        df = feat[pick].tail(200)
        st.line_chart(df[["Close", "ma20", "ma50", "ma100"]])
        st.dataframe(df, use_container_width=True)


if run_btn:
    _run()
else:
    st.info("Set parameters in the sidebar and click **Run engine** to start.")
