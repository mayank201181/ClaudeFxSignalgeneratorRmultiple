# ClaudeFxSignalgeneratorRmultiple

A static web app that pulls live FX data from Yahoo Finance and generates
trading signals (SMA crossover + RSI) for multiple currency pairs at once.

## Live site

Once GitHub Pages is enabled, the app is served at:

> https://mayank201181.github.io/ClaudeFxSignalgeneratorRmultiple/

## Running locally

It's a plain static site — open `index.html` in a browser, or:

```sh
python3 -m http.server 8000
# then visit http://localhost:8000
```

## Deployment

A GitHub Actions workflow (`.github/workflows/pages.yml`) deploys to GitHub
Pages on every push to `main`. To enable it once:

1. In the repo, go to **Settings → Pages**.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.

The next push to `main` will publish the site.

## How it works

- Fetches OHLC data from `query1.finance.yahoo.com` via a public CORS proxy
  (falls back across `corsproxy.io`, `allorigins.win`, `codetabs.com`).
- Computes a fast/slow SMA crossover and a Wilder RSI in the browser.
- Renders price + SMAs on a Chart.js line chart and shows a BUY/SELL/HOLD tag
  per pair.

## Disclaimer

For informational and educational use only. Not financial advice.
