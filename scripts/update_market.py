#!/usr/bin/env python3
"""Refresh IDX candles from Yahoo Finance with batching, retries, and safe file writes."""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

TOP50 = [
    "BBCA","DCII","BREN","BBRI","BYAN","BMRI","MORA","TLKM","AMMN","ASII",
    "DSSA","TPIA","SMMA","DNET","SRAJ","BRPT","BBNI","PANI","CASA","MPRO",
    "UNTR","EMAS","BNLI","ICBP","HMSP","BRIS","IMPC","CDIA","UNVR","BRMS",
    "ADRO","ANTM","AADI","CUAN","MDKA","INDF","GOTO","AMRT","ISAT","CPIN",
    "ADMR","MBMA","SUPR","NCKL","BUMI","MEGA","MTEL","INCO","EXCL","MYOR",
]
LEGACY = ["BKSL","KETR","PTBA","NETV","PTRO","ITMG"]
ADDITIONAL = ["JGLE"]
TICKERS = TOP50 + LEGACY + ADDITIONAL


def finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temp.replace(path)


def split_frame(frame: pd.DataFrame, yahoo_symbol: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    if isinstance(frame.columns, pd.MultiIndex):
        level0 = list(frame.columns.get_level_values(0))
        level1 = list(frame.columns.get_level_values(1))
        if yahoo_symbol in level0:
            return frame[yahoo_symbol].copy()
        if yahoo_symbol in level1:
            return frame.xs(yahoo_symbol, axis=1, level=1).copy()
    return frame.copy()


def bars_from_frame(symbol: str, frame: pd.DataFrame, mode: str) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    if frame.empty:
        return bars
    required = ["Open", "High", "Low", "Close"]
    if not all(column in frame.columns for column in required):
        return bars
    for index, row in frame.iterrows():
        values = [row.get(column) for column in required]
        if not all(finite(value) for value in values):
            continue
        dt = pd.Timestamp(index)
        if dt.tzinfo is None:
            dt = dt.tz_localize("UTC")
        else:
            dt = dt.tz_convert("UTC")
        stamp = int(dt.timestamp())
        volume = row.get("Volume", 0)
        common = {
            "open": float(values[0]), "high": float(values[1]),
            "low": float(values[2]), "close": float(values[3]),
            "volume": int(float(volume)) if finite(volume) else 0,
        }
        if mode == "daily":
            common.update({"time": dt.strftime("%Y-%m-%d"), "timestamp": stamp})
        else:
            jakarta = dt.tz_convert("Asia/Jakarta")
            common.update({"time": stamp, "day": jakarta.strftime("%Y-%m-%d"), "dateTime": jakarta.isoformat()})
        bars.append(common)
    # Yahoo occasionally returns duplicate rows.
    key = "time"
    unique = {bar[key]: bar for bar in bars}
    return [unique[k] for k in sorted(unique)]


def metadata(symbol: str) -> dict[str, Any]:
    return {"longName": f"{symbol} • IDX", "exchangeName": "IDX"}


def record(symbol: str, bars: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    now = int(time.time())
    if mode == "daily":
        return {
            "symbol": symbol, "updatedAt": now, "latestCandle": bars[-1]["time"],
            "source": "Yahoo Finance", "meta": metadata(symbol), "bars": bars,
        }
    return {
        "symbol": symbol, "updatedAt": now, "latestCandleTime": bars[-1]["time"],
        "latestPrice": bars[-1]["close"], "source": "Yahoo Finance 5m", "interval": "5m",
        "meta": metadata(symbol), "bars": bars,
    }


def download_batch(symbols: list[str], mode: str) -> pd.DataFrame:
    yahoo_symbols = [f"{symbol}.JK" for symbol in symbols]
    kwargs = dict(
        tickers=" ".join(yahoo_symbols),
        period="2y" if mode == "daily" else "5d",
        interval="1d" if mode == "daily" else "5m",
        group_by="ticker", auto_adjust=False, actions=False,
        progress=False, threads=True, timeout=40,
    )
    return yf.download(**kwargs)


def download_single(symbol: str, mode: str) -> pd.DataFrame:
    ticker = yf.Ticker(f"{symbol}.JK")
    return ticker.history(
        period="2y" if mode == "daily" else "5d",
        interval="1d" if mode == "daily" else "5m",
        auto_adjust=False, actions=False, timeout=40,
    )


def refresh(mode: str) -> None:
    output = Path("data" if mode == "daily" else "data/intraday")
    output.mkdir(parents=True, exist_ok=True)
    status_rows: dict[str, dict[str, Any]] = {
        symbol: {"symbol": symbol, "success": False, "error": "not processed"} for symbol in TICKERS
    }
    batch_size = 8
    for offset in range(0, len(TICKERS), batch_size):
        symbols = TICKERS[offset:offset + batch_size]
        try:
            frame = download_batch(symbols, mode)
        except Exception as exc:
            frame = pd.DataFrame()
            print(f"Batch {symbols} failed: {exc}")
        for symbol in symbols:
            yahoo_symbol = f"{symbol}.JK"
            bars = bars_from_frame(symbol, split_frame(frame, yahoo_symbol), mode)
            minimum = 5 if mode == "daily" else 1
            error = None
            if len(bars) < minimum:
                for attempt in range(3):
                    try:
                        single = download_single(symbol, mode)
                        bars = bars_from_frame(symbol, single, mode)
                        if len(bars) >= minimum:
                            break
                    except Exception as exc:
                        error = str(exc)
                    time.sleep(2 + attempt * 3 + random.random())
            if len(bars) >= minimum:
                atomic_json(output / f"{symbol}.json", record(symbol, bars, mode))
                status_rows[symbol] = {
                    "symbol": symbol, "success": True, "candles": len(bars),
                    "latest": bars[-1]["time"],
                }
                print(f"Saved {symbol}: {len(bars)} {mode} candles")
            else:
                # Keep an older valid file if one exists; never replace it with empty data.
                status_rows[symbol] = {
                    "symbol": symbol, "success": False,
                    "error": error or "Yahoo returned no valid candles; old cache preserved",
                }
                print(f"Skipped {symbol}: {status_rows[symbol]['error']}")
            time.sleep(0.8 + random.random() * 0.5)
        time.sleep(4 + random.random() * 2)

    rows = [status_rows[symbol] for symbol in TICKERS]
    payload = {
        "updatedAt": int(time.time()), "mode": mode,
        "top50Count": len(TOP50), "additionalCount": len(LEGACY) + len(ADDITIONAL),
        "tickerCount": len(TICKERS), "successCount": sum(1 for row in rows if row["success"]),
        "failedCount": sum(1 for row in rows if not row["success"]), "results": rows,
    }
    atomic_json(Path("data") / f"refresh-status-{mode}.json", payload)
    if payload["successCount"] == 0:
        raise SystemExit(f"No {mode} data could be refreshed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["daily", "intraday"], required=True)
    args = parser.parse_args()
    refresh(args.mode)


if __name__ == "__main__":
    main()
