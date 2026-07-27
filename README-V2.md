# IDX Chart Lab V2

V2 fixes the recurring empty-chart and stale-intraday issues:

- One ticker source: `config/tickers.json` (50 market-cap names + legacy names + JGLE).
- Concurrent Yahoo downloads, so 57 tickers can finish within one workflow window.
- Every failed ticker preserves its previous valid JSON; no empty `bars: []` overwrite.
- Intraday cron requests every 5 minutes and cancels an older run if a newer run starts.
- UI reports the actual data age and warns when intraday is older than 20 minutes.
- Daily refresh runs later after market close to reduce missing final candles.

## First installation

1. Upload all files to repository root and commit to `main`.
2. Open **Actions** and enable workflows if GitHub asks.
3. Run **V2 Refresh IDX daily data** manually once.
4. Run **V2 Refresh IDX 5-minute data** manually once.
5. Wait for GitHub Pages deployment, then hard-refresh the page.

## Important limitation

GitHub scheduled workflows are not guaranteed to start at the exact minute. Yahoo Finance data can also be delayed. The V2 target is usually 5–15 minutes, but it cannot guarantee real-time exchange data.
