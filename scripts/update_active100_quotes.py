#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import json
import math
import random
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "tickers.json"
OUT = ROOT / "data" / "quotes.json"
JAKARTA = ZoneInfo("Asia/Jakarta")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}


def finite(v):
    return isinstance(v, (int, float)) and math.isfinite(v)


def active_tickers():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    symbols = cfg.get("all") or cfg.get("active100") or []
    symbols = [str(x).upper().strip().replace(".JK", "") for x in symbols if str(x).strip()]
    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        raise RuntimeError("config/tickers.json contains no active tickers")
    if len(symbols) > 120:
        raise RuntimeError(f"Safety stop: expected <=120 active tickers, got {len(symbols)}")
    return symbols


def get_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)


def parse(symbol, result):
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    rows = []
    for i, stamp in enumerate(timestamps):
        values = []
        for key in ("open", "high", "low", "close"):
            arr = quote.get(key) or []
            values.append(arr[i] if i < len(arr) else None)
        if not all(finite(v) for v in values):
            continue
        va = quote.get("volume") or []
        volume = va[i] if i < len(va) else 0
        dt = datetime.fromtimestamp(stamp).astimezone(JAKARTA)
        rows.append({
            "time": int(stamp),
            "day": dt.strftime("%Y-%m-%d"),
            "open": values[0],
            "high": values[1],
            "low": values[2],
            "close": values[3],
            "volume": volume if finite(volume) else 0,
        })
    if not rows:
        raise RuntimeError("no valid 5-minute candles")

    latest_day = rows[-1]["day"]
    session = [row for row in rows if row["day"] == latest_day]
    if not session:
        raise RuntimeError("latest session is empty")

    now = int(time.time())
    return {
        "symbol": symbol,
        "updatedAt": now,
        "latestCandleTime": session[-1]["time"],
        "day": latest_day,
        "open": session[0]["open"],
        "high": max(r["high"] for r in session),
        "low": min(r["low"] for r in session),
        "close": session[-1]["close"],
        "volume": sum((r["volume"] or 0) for r in session),
        "bars": session,
    }


def download(symbol):
    errors = []
    # 5d prevents an empty result around session boundaries/holidays while still
    # letting parse() select Yahoo's latest available IDX trading day.
    for attempt in range(3):
        for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
            url = (
                f"https://{host}/v8/finance/chart/{symbol}.JK"
                "?range=5d&interval=5m&includePrePost=false&events=history"
            )
            try:
                payload = get_json(url)
                chart = payload.get("chart") or {}
                if chart.get("error"):
                    raise RuntimeError(str(chart["error"]))
                results = chart.get("result") or []
                if not results:
                    raise RuntimeError("Yahoo returned no result")
                return symbol, parse(symbol, results[0]), None
            except Exception as exc:
                errors.append(f"{host}: {exc}")
        time.sleep(0.8 + attempt * 1.4 + random.random() * 0.5)
    return symbol, None, " | ".join(errors[-4:])


def load_old():
    try:
        old = json.loads(OUT.read_text(encoding="utf-8"))
        if not isinstance(old.get("quotes"), dict):
            raise ValueError("invalid old quote book")
        return old
    except Exception:
        return {"quotes": {}}


def main():
    symbols = active_tickers()
    old = load_old()
    quotes = dict(old.get("quotes") or {})
    successes = {}
    failures = {}

    print(f"Refreshing {len(symbols)} active IDX tickers with 8 workers")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(download, symbol): symbol for symbol in symbols}
        for future in concurrent.futures.as_completed(futures):
            symbol, quote, error = future.result()
            if quote:
                successes[symbol] = quote
                quotes[symbol] = quote
                print(f"OK   {symbol}: {quote['day']} close={quote['close']}")
            else:
                failures[symbol] = error
                print(f"FAIL {symbol}: {error}")

    # Do not publish a nearly-empty refresh caused by a Yahoo outage.
    minimum_success = max(50, int(len(symbols) * 0.70))
    if len(successes) < minimum_success:
        raise SystemExit(
            f"Yahoo refresh rejected: only {len(successes)}/{len(symbols)} succeeded; "
            f"minimum is {minimum_success}. Existing quotes.json left unchanged."
        )

    now = int(time.time())
    latest_days = {}
    for symbol, quote in successes.items():
        latest_days[quote["day"]] = latest_days.get(quote["day"], 0) + 1

    payload = {
        "version": 4.0,
        "schema": "idx-active100-quotes-v1",
        "updatedAt": now,
        "refresh": {
            "tickerCount": len(symbols),
            "successCount": len(successes),
            "failedCount": len(failures),
            "failed": sorted(failures),
            "latestDays": latest_days,
            "source": "Yahoo Finance chart v8, 5m, 5d",
        },
        "quotes": quotes,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(OUT)

    print("---")
    print(f"SUCCESS {len(successes)}/{len(symbols)}")
    print(f"LATEST DAYS {json.dumps(latest_days, sort_keys=True)}")
    if "DSSA" in successes:
        print(f"DSSA {successes['DSSA']['day']} {successes['DSSA']['latestCandleTime']}")


if __name__ == "__main__":
    main()
