#!/usr/bin/env python3
from __future__ import annotations
import argparse, concurrent.futures, json, math, random, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "tickers.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; IDX-Chart-Lab-Full/3.2)",
    "Accept": "application/json,text/plain,*/*",
}

def finite(v): return isinstance(v, (int, float)) and math.isfinite(v)

def tickers():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    seq = cfg.get("all") or []
    return sorted(set(str(x).upper().strip() for x in seq if str(x).strip()))

def parse(symbol, result):
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    ts = result.get("timestamp") or []
    bars = []
    for i, stamp in enumerate(ts):
        vals = []
        for key in ("open", "high", "low", "close"):
            arr = quote.get(key) or []
            vals.append(arr[i] if i < len(arr) else None)
        if not all(finite(v) for v in vals):
            continue
        volumes = quote.get("volume") or []
        volume = volumes[i] if i < len(volumes) else 0
        bars.append({
            "time": datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d"),
            "timestamp": stamp,
            "open": vals[0], "high": vals[1], "low": vals[2], "close": vals[3],
            "volume": volume if finite(volume) else 0,
        })
    if len(bars) < 5:
        raise ValueError("not enough valid daily candles")
    meta = result.get("meta") or {}
    return {
        "version": 3.2,
        "symbol": symbol,
        "updatedAt": int(time.time()),
        "source": "Yahoo Finance",
        "latestCandle": bars[-1]["time"],
        "meta": {
            "longName": meta.get("longName") or meta.get("shortName") or f"{symbol} • IDX",
            "exchangeName": meta.get("fullExchangeName") or "IDX",
            "regularMarketTime": meta.get("regularMarketTime"),
            "regularMarketPrice": meta.get("regularMarketPrice"),
        },
        "bars": bars,
    }

def download(symbol):
    last = None
    for attempt in range(4):
        for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
            url = (
                f"https://{host}/v8/finance/chart/{symbol}.JK?"
                "range=2y&interval=1d&events=history&includeAdjustedClose=true"
            )
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=30) as r:
                    payload = json.load(r)
                chart = payload.get("chart") or {}
                if chart.get("error"):
                    raise RuntimeError(str(chart["error"]))
                results = chart.get("result") or []
                if not results:
                    raise ValueError("Yahoo returned no result")
                return symbol, parse(symbol, results[0]), None
            except Exception as exc:
                last = exc
        time.sleep(1.0 + attempt * 1.8 + random.random())
    return symbol, None, str(last or "unknown error")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard-index", type=int, required=True)
    ap.add_argument("--shard-count", type=int, required=True)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    all_symbols = tickers()
    selected = [s for i, s in enumerate(all_symbols) if i % args.shard_count == args.shard_index]
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as pool:
        futures = {pool.submit(download, s): s for s in selected}
        for future in concurrent.futures.as_completed(futures):
            symbol, payload, error = future.result()
            if payload:
                (out / f"{symbol}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                results.append({"symbol": symbol, "success": True, "candles": len(payload["bars"])})
                print(f"OK {symbol}: {len(payload['bars'])}")
            else:
                results.append({"symbol": symbol, "success": False, "error": error})
                print(f"FAIL {symbol}: {error}")

    (out / f"_status_shard_{args.shard_index}.json").write_text(
        json.dumps({
            "shard": args.shard_index,
            "count": len(selected),
            "success": sum(1 for r in results if r["success"]),
            "failed": sum(1 for r in results if not r["success"]),
            "results": sorted(results, key=lambda x: x["symbol"]),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

if __name__ == "__main__":
    main()
