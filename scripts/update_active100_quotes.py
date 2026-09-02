#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import random
import time
import urllib.parse
import urllib.request
import urllib.error
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
    "Accept-Language": "en-US,en;q=0.9",
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


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def get_json(url, timeout=30):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def parse(symbol, response):
    if not response:
        return None
    timestamps = response.get("timestamp") or []
    quote = ((response.get("indicators") or {}).get("quote") or [{}])[0]
    rows = []
    for i, stamp in enumerate(timestamps):
        values = []
        for key in ("open", "high", "low", "close"):
            arr = quote.get(key) or []
            values.append(arr[i] if i < len(arr) else None)
        if not all(finite(v) for v in values):
            continue
        volumes = quote.get("volume") or []
        volume = volumes[i] if i < len(volumes) else 0
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
        return None
    latest_day = rows[-1]["day"]
    session = [row for row in rows if row["day"] == latest_day]
    if not session:
        return None
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


def fetch_spark_batch(symbols):
    joined = ",".join(f"{s}.JK" for s in symbols)
    params = urllib.parse.urlencode({
        "symbols": joined,
        "range": "5d",
        "interval": "5m",
    })
    errors = []
    for attempt in range(4):
        for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
            url = f"https://{host}/v7/finance/spark?{params}"
            try:
                payload = get_json(url)
                spark = payload.get("spark") or {}
                if spark.get("error"):
                    raise RuntimeError(str(spark["error"]))
                result = spark.get("result") or []
                if not result:
                    raise RuntimeError("empty spark result")
                return result
            except urllib.error.HTTPError as exc:
                errors.append(f"{host}: HTTP {exc.code}")
                if exc.code == 429:
                    retry_after = exc.headers.get("Retry-After")
                    try:
                        wait = max(8, min(45, int(retry_after))) if retry_after else 12 + attempt * 8
                    except Exception:
                        wait = 12 + attempt * 8
                    print(f"Yahoo 429 for batch {symbols[0]}..{symbols[-1]}; cooldown {wait}s")
                    time.sleep(wait)
            except Exception as exc:
                errors.append(f"{host}: {exc}")
        time.sleep(3 + attempt * 3 + random.random() * 2)
    raise RuntimeError(" | ".join(errors[-6:]))


def fetch_one(symbol):
    errors = []
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{symbol}.JK?range=5d&interval=5m&includePrePost=false&events=history"
        try:
            payload = get_json(url)
            results = ((payload.get("chart") or {}).get("result") or [])
            if results:
                return parse(symbol, results[0])
        except Exception as exc:
            errors.append(str(exc))
    return None


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
    failures = set(symbols)

    print(f"Refreshing {len(symbols)} active IDX tickers in Yahoo Spark batches of 20")

    for batch_no, group in enumerate(chunks(symbols, 20), start=1):
        print(f"BATCH {batch_no}: {group[0]}..{group[-1]} ({len(group)} tickers)")
        try:
            items = fetch_spark_batch(group)
            returned = {}
            for item in items:
                raw_symbol = str(item.get("symbol") or "").upper().replace(".JK", "")
                response = (item.get("response") or [None])[0]
                if raw_symbol:
                    returned[raw_symbol] = response
            for symbol in group:
                q = parse(symbol, returned.get(symbol))
                if q:
                    successes[symbol] = q
                    quotes[symbol] = q
                    failures.discard(symbol)
                    print(f"OK   {symbol}: {q['day']} close={q['close']}")
        except Exception as exc:
            print(f"BATCH FAIL {group[0]}..{group[-1]}: {exc}")
        # Keep the GitHub runner well below Yahoo request pressure.
        time.sleep(5 + random.random() * 3)

    # Only a small number of misses get an individual retry. This is deliberately
    # capped to avoid recreating the 100-request rate-limit problem.
    retry_symbols = list(sorted(failures))[:8]
    if retry_symbols:
        print(f"Individual fallback for at most {len(retry_symbols)} tickers")
        time.sleep(12)
        for symbol in retry_symbols:
            q = fetch_one(symbol)
            if q:
                successes[symbol] = q
                quotes[symbol] = q
                failures.discard(symbol)
                print(f"FALLBACK OK {symbol}: {q['day']} close={q['close']}")
            time.sleep(2)

    minimum_success = max(40, int(len(symbols) * 0.60))
    if len(successes) < minimum_success:
        raise SystemExit(
            f"Yahoo refresh rejected: only {len(successes)}/{len(symbols)} succeeded; "
            f"minimum is {minimum_success}. Existing quotes.json left unchanged."
        )

    now = int(time.time())
    latest_days = {}
    for quote in successes.values():
        latest_days[quote["day"]] = latest_days.get(quote["day"], 0) + 1

    payload = {
        "version": 4.1,
        "schema": "idx-active100-quotes-v2",
        "updatedAt": now,
        "refresh": {
            "tickerCount": len(symbols),
            "successCount": len(successes),
            "failedCount": len(failures),
            "failed": sorted(failures),
            "latestDays": latest_days,
            "source": "Yahoo Finance spark v7 batch, 5m, 5d",
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
    if "DSSA" in quotes:
        q = quotes["DSSA"]
        print(f"DSSA {q.get('day')} close={q.get('close')} candle={q.get('latestCandleTime')}")


if __name__ == "__main__":
    main()
