# ClaudeFxSignalgeneratorRmultiple

Asymmetric R-multiple trade-setup engine for G10 FX, with a Streamlit web UI
on top. Pulls daily OHLC from Yahoo Finance via `yfinance`, runs a
barrier-based backtest on every signal/pair, and reports pooled EV in R units.

## Files

| File                    | Purpose                                                                |
|-------------------------|------------------------------------------------------------------------|
| `g10_setup_engine.py`   | Pure engine: data, features, signals, backtest, metrics, live blotter. |
| `streamlit_app.py`      | Streamlit UI wrapping the engine (universe, start date, target R).     |
| `requirements.txt`      | Python dependencies for both local runs and Streamlit Cloud.           |

## Run locally

```sh
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Or run the engine standalone in a terminal (prints summary tables + today's
blotter):

```sh
python g10_setup_engine.py
```

## Deploy to a public URL (Streamlit Community Cloud)

Streamlit Cloud is free and connects directly to GitHub. One-time setup:

1. Go to https://share.streamlit.io and sign in with GitHub.
2. Click **New app**.
3. Pick this repo (`mayank201181/ClaudeFxSignalgeneratorRmultiple`),
   branch **main**, main file **`streamlit_app.py`**.
4. Click **Deploy**.

Streamlit will install `requirements.txt` and give you a public URL of the
form `https://<your-slug>.streamlit.app`. Every push to `main` redeploys
automatically.

## Engine notes

- Trades are normalised to **R = |entry - stop|**, so signals pool across the
  whole complex.
- Backtest is **barrier-based**: for each signal we ask whether price reached
  +kR before -1R within `MAX_HORIZON` trading days.
- Daily OHLC can't say whether the stop or the target was touched first inside
  a bar. The default convention is **conservative** (stop wins same-bar ties)
  so EV is understated rather than flattered. Calibrate the haircut later
  with tick data.
- Reports MAE / MFE so stop and target placement is driven by evidence.

## Disclaimer

For informational and educational use only. Not financial advice.
