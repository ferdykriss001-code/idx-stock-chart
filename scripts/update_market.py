#!/usr/bin/env python3
"""IDX Chart Lab V2 market updater.

Uses Yahoo's chart endpoint concurrently, preserves older valid files on errors,
and writes a refresh status report for diagnostics.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import random
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "tickers.json"
JAKARTA = ZoneInfo("Asia/Jakarta")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; IDX-Chart-Lab-V2/2.0)",
    "Accept": "application/json,text/plain,*/*",
}


def load_tickers() -> list[str]:
    payload = json.loads(CONFIG.read_text(encoding="utf-8"))
    combined = payload.get("top50", []) + payload.get("legacy", []) + payload.get("additional", [])
    return list(dict.fromkeys(str(item).upper().strip() for item in combined if str(item).strip()))


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temp.replace(path)


def endpoint(symbol: str, mode: str, host: str) -> str:
    if mode == "daily":
        query = "range=2y&interval=1d&events=history&includeAdjustedClose=true"
    else:
        query = "range=5d&interval=5m&events=history&includePrePost=false"
    return f"https://{host}/v8/finance/chart/{symbol}.JK?{query}"


def parse(symbol: str, mode: str, result: dict[str, Any]) -> dict[str, Any]:
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    timestamps = result.get("timestamp") or []
    bars: list[dict[str, Any]] = []
    for idx, stamp in enumerate(timestamps):
        values = []
        for key in ("open", "high", "low", "close"):
            series = quote.get(key) or []
            values.append(series[idx] if idx < len(series) else None)
        if not all(finite(value) for value in values):
            continue
        volumes = quote.get("volume") or []
        volume = volumes[idx] if idx < len(volumes) else 0
        common = {
            "open": values[0], "high": values[1], "low": values[2], "close": values[3],
            "volume": volume if finite(volume) else 0,
        }
        if mode == "daily":
            common.update({"time": datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d"), "timestamp": stamp})
        else:
            local_dt = datetime.fromtimestamp(stamp, timezone.utc).astimezone(JAKARTA)
            common.update({"time": stamp, "day": local_dt.strftime("%Y-%m-%d"), "dateTime": local_dt.isoformat()})
        bars.append(common)
    bars = list({bar["time"]: bar for bar in bars}.values())
    bars.sort(key=lambda bar: bar["time"])
    if not bars:
        raise ValueError("Yahoo returned no valid candles")
    meta = result.get("meta") or {}
    now = int(time.time())
    base = {
        "symbol": symbol,
        "updatedAt": now,
        "source": "Yahoo Finance" if mode == "daily" else "Yahoo Finance 5m",
        "meta": {
            "longName": meta.get("longName") or meta.get("shortName") or f"{symbol} • IDX",
            "exchangeName": meta.get("fullExchangeName") or "IDX",
            "regularMarketTime": meta.get("regularMarketTime"),
            "regularMarketPrice": meta.get("regularMarketPrice"),
        },
        "bars": bars,
    }
    if mode == "daily":
        base["latestCandle"] = bars[-1]["time"]
    else:
        base.update({"interval": "5m", "latestCandleTime": bars[-1]["time"], "latestPrice": bars[-1]["close"]})
    return base


def download(symbol: str, mode: str) -> tuple[str, dict[str, Any] | None, str | None]:
    last_error: Exception | None = None
    for attempt in range(3):
        for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
            try:
                request = urllib.request.Request(endpoint(symbol, mode, host), headers=HEADERS)
                with urllib.request.urlopen(request, timeout=25) as response:
                    payload = json.load(response)
                chart = payload.get("chart") or {}
                if chart.get("error"):
                    raise RuntimeError(str(chart["error"]))
                results = chart.get("result") or []
                if not results:
                    raise ValueError("Yahoo returned no result")
                return symbol, parse(symbol, mode, results[0]), None
            except Exception as exc:  # preserve diagnostics per ticker
                last_error = exc
        time.sleep(1.2 + attempt * 1.8 + random.random())
    return symbol, None, str(last_error or "unknown error")


def refresh(mode: str, workers: int) -> None:
    tickers = load_tickers()
    output = ROOT / ("data" if mode == "daily" else "data/intraday")
    output.mkdir(parents=True, exist_ok=True)
    started = int(time.time())
    results: list[dict[str, Any]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(download, symbol, mode): symbol for symbol in tickers}
        for future in concurrent.futures.as_completed(futures):
            symbol, record, error = future.result()
            path = output / f"{symbol}.json"
            if record is not None:
                atomic_json(path, record)
                results.append({"symbol": symbol, "success": True, "candles": len(record["bars"]), "latest": record["bars"][-1]["time"]})
                print(f"Saved {symbol}: {len(record['bars'])} {mode} candles")
            else:
                old_valid = False
                try:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                    old_valid = bool(existing.get("bars"))
                except Exception:
                    pass
                results.append({"symbol": symbol, "success": False, "oldCachePreserved": old_valid, "error": error})
                print(f"Skipped {symbol}: {error}; old_valid={old_valid}")

    results.sort(key=lambda row: row["symbol"])
    success_count = sum(1 for row in results if row["success"])
    payload = {
        "version": 2,
        "mode": mode,
        "startedAt": started,
        "updatedAt": int(time.time()),
        "durationSeconds": int(time.time()) - started,
        "tickerCount": len(tickers),
        "successCount": success_count,
        "failedCount": len(tickers) - success_count,
        "results": results,
    }
    atomic_json(ROOT / "data" / f"refresh-status-{mode}.json", payload)
    if success_count == 0:
        raise SystemExit(f"No {mode} ticker could be refreshed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("daily", "intraday"), required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    refresh(args.mode, max(1, min(args.workers, 12)))


if __name__ == "__main__":
    main()
