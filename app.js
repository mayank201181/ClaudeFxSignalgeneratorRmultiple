// FX Signal Generator - Yahoo Finance + SMA crossover + RSI

const PROXIES = [
  (url) => `https://corsproxy.io/?${encodeURIComponent(url)}`,
  (url) => `https://api.allorigins.win/raw?url=${encodeURIComponent(url)}`,
  (url) => `https://api.codetabs.com/v1/proxy?quest=${encodeURIComponent(url)}`,
];

async function fetchYahooChart(symbol, interval, range) {
  const yahooUrl = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?interval=${interval}&range=${range}`;
  let lastErr;
  for (const wrap of PROXIES) {
    try {
      const res = await fetch(wrap(yahooUrl), { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!data?.chart?.result?.length) throw new Error("Empty response");
      return data.chart.result[0];
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr ?? new Error("All proxies failed");
}

function parseSeries(result) {
  const ts = result.timestamp || [];
  const closes = result.indicators?.quote?.[0]?.close || [];
  const out = [];
  for (let i = 0; i < ts.length; i++) {
    const c = closes[i];
    if (c == null) continue;
    out.push({ t: ts[i] * 1000, c });
  }
  return out;
}

function sma(values, period) {
  const out = new Array(values.length).fill(null);
  if (values.length < period) return out;
  let sum = 0;
  for (let i = 0; i < period; i++) sum += values[i];
  out[period - 1] = sum / period;
  for (let i = period; i < values.length; i++) {
    sum += values[i] - values[i - period];
    out[i] = sum / period;
  }
  return out;
}

function rsi(values, period) {
  const out = new Array(values.length).fill(null);
  if (values.length < period + 1) return out;
  let gain = 0, loss = 0;
  for (let i = 1; i <= period; i++) {
    const d = values[i] - values[i - 1];
    if (d >= 0) gain += d; else loss -= d;
  }
  gain /= period; loss /= period;
  out[period] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
  for (let i = period + 1; i < values.length; i++) {
    const d = values[i] - values[i - 1];
    const g = d > 0 ? d : 0;
    const l = d < 0 ? -d : 0;
    gain = (gain * (period - 1) + g) / period;
    loss = (loss * (period - 1) + l) / period;
    out[i] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
  }
  return out;
}

function decideSignal({ fast, slow, fastPrev, slowPrev, rsiNow }) {
  if (fast == null || slow == null || fastPrev == null || slowPrev == null || rsiNow == null) {
    return { signal: "HOLD", reason: "Not enough data" };
  }
  const crossUp   = fastPrev <= slowPrev && fast >  slow;
  const crossDown = fastPrev >= slowPrev && fast <  slow;
  if (crossUp && rsiNow < 70)   return { signal: "BUY",  reason: "Fast SMA crossed above slow SMA, RSI not overbought" };
  if (crossDown && rsiNow > 30) return { signal: "SELL", reason: "Fast SMA crossed below slow SMA, RSI not oversold" };
  if (fast > slow && rsiNow < 30) return { signal: "BUY",  reason: "Uptrend with oversold RSI" };
  if (fast < slow && rsiNow > 70) return { signal: "SELL", reason: "Downtrend with overbought RSI" };
  if (fast > slow) return { signal: "HOLD", reason: "Uptrend, no fresh signal" };
  if (fast < slow) return { signal: "HOLD", reason: "Downtrend, no fresh signal" };
  return { signal: "HOLD", reason: "Neutral" };
}

function formatPrice(p, symbol) {
  if (p == null) return "—";
  const digits = symbol.includes("JPY") ? 3 : 5;
  return p.toFixed(digits);
}

function prettyName(symbol) {
  const base = symbol.replace("=X", "");
  if (base.length === 6) return `${base.slice(0,3)}/${base.slice(3)}`;
  return base;
}

const charts = new Map();

function renderCard(symbol, series, params) {
  const closes = series.map(p => p.c);
  const fastArr = sma(closes, params.fast);
  const slowArr = sma(closes, params.slow);
  const rsiArr  = rsi(closes, params.rsiPeriod);

  const n = closes.length;
  const decision = decideSignal({
    fast: fastArr[n - 1], slow: slowArr[n - 1],
    fastPrev: fastArr[n - 2], slowPrev: slowArr[n - 2],
    rsiNow: rsiArr[n - 1],
  });

  const last = closes[n - 1];
  const prev = closes[n - 2];
  const chgPct = prev ? ((last - prev) / prev) * 100 : 0;

  const results = document.getElementById("results");
  let card = document.getElementById(`card-${symbol}`);
  if (!card) {
    card = document.createElement("div");
    card.className = "card";
    card.id = `card-${symbol}`;
    card.innerHTML = `
      <div class="card-header">
        <h3>${prettyName(symbol)}</h3>
        <span class="price"></span>
      </div>
      <div class="signal-row"></div>
      <div class="metrics"></div>
      <div class="chart-wrap"><canvas></canvas></div>
    `;
    results.appendChild(card);
  }

  card.querySelector(".price").textContent =
    `${formatPrice(last, symbol)}  (${chgPct >= 0 ? "+" : ""}${chgPct.toFixed(2)}%)`;

  card.querySelector(".signal-row").innerHTML =
    `<span class="signal ${decision.signal}">${decision.signal}</span>
     <span style="margin-left:8px;color:var(--muted);font-size:12px">${decision.reason}</span>`;

  card.querySelector(".metrics").innerHTML = `
    <div>Fast SMA(${params.fast}): <b>${formatPrice(fastArr[n-1], symbol)}</b></div>
    <div>Slow SMA(${params.slow}): <b>${formatPrice(slowArr[n-1], symbol)}</b></div>
    <div>RSI(${params.rsiPeriod}): <b>${rsiArr[n-1] != null ? rsiArr[n-1].toFixed(1) : "—"}</b></div>
    <div>Bars: <b>${n}</b></div>
  `;

  const canvas = card.querySelector("canvas");
  if (charts.has(symbol)) charts.get(symbol).destroy();
  const labels = series.map(p => p.t);
  const chart = new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "Close", data: closes, borderColor: "#6ea8ff", borderWidth: 1.5, pointRadius: 0, tension: 0.1 },
        { label: `SMA${params.fast}`, data: fastArr, borderColor: "#34c759", borderWidth: 1, pointRadius: 0, borderDash: [4,4] },
        { label: `SMA${params.slow}`, data: slowArr, borderColor: "#ff9f0a", borderWidth: 1, pointRadius: 0, borderDash: [6,3] },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {
        x: { type: "time", ticks: { color: "#98a0b3", maxTicksLimit: 6 }, grid: { color: "#2a3150" } },
        y: { ticks: { color: "#98a0b3" }, grid: { color: "#2a3150" } },
      },
      plugins: {
        legend: { labels: { color: "#e6e8ef", boxWidth: 12, font: { size: 11 } } },
        tooltip: { mode: "index", intersect: false },
      },
    },
  });
  charts.set(symbol, chart);
}

async function run() {
  const btn = document.getElementById("runBtn");
  const status = document.getElementById("status");
  const results = document.getElementById("results");

  const symbols = Array.from(document.getElementById("pairs").selectedOptions).map(o => o.value);
  if (!symbols.length) {
    status.textContent = "Pick at least one pair.";
    status.className = "status error";
    return;
  }

  const params = {
    interval: document.getElementById("interval").value,
    range: document.getElementById("range").value,
    fast: Math.max(2, parseInt(document.getElementById("fastSma").value, 10) || 20),
    slow: Math.max(3, parseInt(document.getElementById("slowSma").value, 10) || 50),
    rsiPeriod: Math.max(2, parseInt(document.getElementById("rsiPeriod").value, 10) || 14),
  };
  if (params.fast >= params.slow) {
    status.textContent = "Fast SMA must be less than slow SMA.";
    status.className = "status error";
    return;
  }

  btn.disabled = true;
  status.className = "status";
  status.textContent = `Fetching ${symbols.length} pair(s) from Yahoo Finance...`;
  results.innerHTML = "";
  charts.clear();

  const settled = await Promise.allSettled(
    symbols.map(async (s) => {
      const r = await fetchYahooChart(s, params.interval, params.range);
      return { s, series: parseSeries(r) };
    })
  );

  const failures = [];
  for (let i = 0; i < settled.length; i++) {
    const res = settled[i];
    if (res.status === "fulfilled" && res.value.series.length > 2) {
      renderCard(res.value.s, res.value.series, params);
    } else {
      failures.push(symbols[i]);
    }
  }

  if (failures.length === settled.length) {
    status.className = "status error";
    status.textContent = `Failed to load data for: ${failures.join(", ")}. Yahoo's public CORS proxies may be rate-limited — try again in a moment.`;
  } else if (failures.length) {
    status.textContent = `Loaded ${settled.length - failures.length} of ${settled.length}. Failed: ${failures.join(", ")}.`;
  } else {
    const ts = new Date().toLocaleTimeString();
    status.textContent = `Loaded ${settled.length} pair(s) at ${ts}.`;
  }
  btn.disabled = false;
}

document.getElementById("runBtn").addEventListener("click", run);
window.addEventListener("DOMContentLoaded", run);
