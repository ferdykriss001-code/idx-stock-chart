#!/usr/bin/env python3
"""IDX Chart Lab V2 market updater.

Uses Yahoo's chart endpoint concurrently, preserves older valid files on errors,
supports deterministic shards, and writes refresh diagnostics for each run.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import random
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "tickers.json"
UNIVERSE = ROOT / "config" / "idx-universe.json"
JAKARTA = ZoneInfo("Asia/Jakarta")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; IDX-Chart-Lab-V2/2.1)",
    "Accept": "application/json,text/plain,*/*",
}


def normalized_symbols(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    output: list[str] = []
    for item in values:
        symbol = str(item or "").upper().strip().removesuffix(".JK")
        if symbol and all(char.isalnum() for char in symbol):
            output.append(symbol)
    return list(dict.fromkeys(output))


def load_tickers() -> list[str]:
    payload = json.loads(CONFIG.read_text(encoding="utf-8"))
    groups = ("top50", "kompas100", "legacy", "additional")
    combined = [symbol for group in groups for symbol in normalized_symbols(payload.get(group, []))]

    try:
        universe = json.loads(UNIVERSE.read_text(encoding="utf-8"))
        universe_tickers = normalized_symbols(universe.get("tickers", []))
        # A short or malformed source response must never replace the known universe.
        if len(universe_tickers) >= 100:
            combined.extend(universe_tickers)
    except (OSError, ValueError, TypeError):
        pass

    return list(dict.fromkeys(combined))


def select_shard(tickers: list[str], shard_index: int, shard_count: int) -> list[str]:
    if shard_count < 1:
        raise ValueError("shard count must be at least 1")
    if shard_index < 0 or shard_index >= shard_count:
        raise ValueError("shard index must be between 0 and shard count - 1")
    ordered = sorted(tickers)
    return [symbol for index, symbol in enumerate(ordered) if index % shard_count == shard_index]


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
        # The app merges 5-minute bars into the daily chart, so one current
        # session is sufficient and keeps Actions artifacts and Git history bounded.
        query = "range=1d&interval=5m&events=history&includePrePost=false"
    return f"https://{host}/v8/finance/chart/{symbol}.JK?{query}"


def parse(symbol: str, mode: str, result: dict[str, Any]) -> dict[str, Any]:
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    timestamps = result.get("timestamp") or []
    bars: list[dict[str, Any]] = []
    for index, stamp in enumerate(timestamps):
        values = []
        for key in ("open", "high", "low", "close"):
            series = quote.get(key) or []
            values.append(series[index] if index < len(series) else None)
        if not all(finite(value) for value in values):
            continue
        volumes = quote.get("volume") or []
        volume = volumes[index] if index < len(volumes) else 0
        common = {
            "open": values[0],
            "high": values[1],
            "low": values[2],
            "close": values[3],
            "volume": volume if finite(volume) else 0,
        }
        if mode == "daily":
            common.update({
                "time": datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d"),
                "timestamp": stamp,
            })
        else:
            local_dt = datetime.fromtimestamp(stamp, timezone.utc).astimezone(JAKARTA)
            common.update({
                "time": stamp,
                "day": local_dt.strftime("%Y-%m-%d"),
                "dateTime": local_dt.isoformat(),
            })
        bars.append(common)

    bars = list({bar["time"]: bar for bar in bars}.values())
    bars.sort(key=lambda bar: bar["time"])
    if not bars:
        raise ValueError("Yahoo returned no valid candles")

    meta = result.get("meta") or {}
    base = {
        "symbol": symbol,
        "updatedAt": int(time.time()),
        "source": "Yahoo Finance" if mode == "daily" else "Yahoo Finance 5m (1d)",
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
        base.update({
            "interval": "5m",
            "latestCandleTime": bars[-1]["time"],
            "latestPrice": bars[-1]["close"],
        })
    return base


def download(symbol: str, mode: str) -> tuple[str, dict[str, Any] | None, str | None]:
    last_error: Exception | None = None
    timeout_seconds = 25 if mode == "daily" else 12
    for attempt in range(3):
        for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
            try:
                request = urllib.request.Request(endpoint(symbol, mode, host), headers=HEADERS)
                with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
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


def existing_cache_metadata(path: Path) -> tuple[bool, int | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        updated_at = payload.get("updatedAt")
        return bool(payload.get("bars")), int(updated_at) if finite(updated_at) else None
    except (OSError, ValueError, TypeError):
        return False, None


def refresh(
    mode: str,
    workers: int,
    shard_index: int,
    shard_count: int,
    artifact_dir: Path | None,
    status_file: Path | None,
    allow_empty: bool,
) -> None:
    all_tickers = load_tickers()
    if not all_tickers:
        raise SystemExit("No tickers configured")

    tickers = select_shard(all_tickers, shard_index, shard_count)
    if not tickers:
        raise SystemExit("Selected shard has no tickers")

    output = ROOT / ("data" if mode == "daily" else "data/intraday")
    output.mkdir(parents=True, exist_ok=True)
    started = int(time.time())
    results: list[dict[str, Any]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(download, symbol, mode): symbol for symbol in tickers}
        for future in concurrent.futures.as_completed(futures):
            symbol = futures[future]
            try:
                symbol, record, error = future.result()
            except Exception as exc:
                record, error = None, f"worker error: {exc}"

            path = output / f"{symbol}.json"
            if record is not None:
                atomic_json(path, record)
                if artifact_dir is not None:
                    artifact_output = artifact_dir / ("data" if mode == "daily" else "data/intraday")
                    atomic_json(artifact_output / f"{symbol}.json", record)
                results.append({
                    "symbol": symbol,
                    "success": True,
                    "updatedAt": record["updatedAt"],
                    "candles": len(record["bars"]),
                    "latest": record["bars"][-1]["time"],
                })
                print(f"Saved {symbol}: {len(record['bars'])} {mode} candles")
            else:
                old_valid, old_updated_at = existing_cache_metadata(path)
                results.append({
                    "symbol": symbol,
                    "success": False,
                    "oldCachePreserved": old_valid,
                    "oldCacheUpdatedAt": old_updated_at,
                    "error": error,
                })
                print(f"Skipped {symbol}: {error}; old_valid={old_valid}")

    results.sort(key=lambda row: row["symbol"])
    success_count = sum(1 for row in results if row["success"])
    payload = {
        "version": 3,
        "mode": mode,
        "startedAt": started,
        "updatedAt": int(time.time()),
        "durationSeconds": int(time.time()) - started,
        "universeTickerCount": len(all_tickers),
        "tickerCount": len(tickers),
        "shardIndex": shard_index,
        "shardCount": shard_count,
        "successCount": success_count,
        "failedCount": len(tickers) - success_count,
        "results": results,
    }

    if status_file is None:
        suffix = f"-shard-{shard_index}" if shard_count > 1 else ""
        status_file = ROOT / "data" / f"refresh-status-{mode}{suffix}.json"
    atomic_json(status_file, payload)

    if success_count == 0 and not allow_empty:
        raise SystemExit(f"No {mode} ticker could be refreshed in shard {shard_index}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("daily", "intraday"), required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--status-file", type=Path)
    parser.add_argument("--allow-empty", action="store_true")
    args = parser.parse_args()
    refresh(
        args.mode,
        max(1, min(args.workers, 12)),
        args.shard_index,
        args.shard_count,
        args.artifact_dir,
        args.status_file,
        args.allow_empty,
    )


if __name__ == "__main__":
    main()
