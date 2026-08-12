#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, math, random, time, urllib.parse, urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "tickers.json"
OUT = ROOT / "data" / "quotes.json"
JAKARTA = ZoneInfo("Asia/Jakarta")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; IDX-Chart-Lab-Full/3.2)",
    "Accept": "application/json,text/plain,*/*",
}

def finite(v): return isinstance(v, (int, float)) and math.isfinite(v)

def all_tickers():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    return sorted(set(str(x).upper().strip() for x in (cfg.get("all") or []) if str(x).strip()))

def load_old():
    try:
        data = json.loads(OUT.read_text(encoding="utf-8"))
        if not isinstance(data.get("quotes"), dict):
            raise ValueError("bad cache")
        return data
    except Exception:
        return {"version": 3.2, "schema": "idx-full-quotes-v1", "updatedAt": None, "quotes": {}, "refresh": {}}

def request_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)

def parse_response(symbol, response, now):
    if not response:
        return None
    timestamps = response.get("timestamp") or []
    quote = ((response.get("indicators") or {}).get("quote") or [{}])[0]
    rows = []
    for i, stamp in enumerate(timestamps):
        vals = []
        for key in ("open", "high", "low", "close"):
            arr = quote.get(key) or []
            vals.append(arr[i] if i < len(arr) else None)
        if not all(finite(v) for v in vals):
            continue
        volumes = quote.get("volume") or []
        vol = volumes[i] if i < len(volumes) else 0
        dt = datetime.fromtimestamp(stamp).astimezone(JAKARTA)
        rows.append({
            "time": stamp,
            "day": dt.strftime("%Y-%m-%d"),
            "open": vals[0], "high": vals[1], "low": vals[2], "close": vals[3],
            "volume": vol if finite(vol) else 0,
        })
    if not rows:
        return None
    latest_day = rows[-1]["day"]
    session = [r for r in rows if r["day"] == latest_day]
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

def spark_batch(symbols):
    joined = ",".join(f"{s}.JK" for s in symbols)
    params = urllib.parse.urlencode({"symbols": joined, "range": "1d", "interval": "5m"})
    errors = []
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = f"https://{host}/v7/finance/spark?{params}"
        try:
            payload = request_json(url)
            spark = payload.get("spark") or {}
            if spark.get("error"):
                raise RuntimeError(str(spark["error"]))
            result = spark.get("result") or []
            return result
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError(" / ".join(errors[-2:]))

def fallback_one(symbol):
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = f"https://{host}/v8/finance/chart/{symbol}.JK?range=1d&interval=5m&includePrePost=false"
        try:
            payload = request_json(url)
            results = ((payload.get("chart") or {}).get("result") or [])
            if results:
                return results[0]
        except Exception:
            pass
    return None

def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i+size]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=35)
    ap.add_argument("--fallback-limit", type=int, default=50)
    args = ap.parse_args()

    symbols = all_tickers()
    cache = load_old()
    cache["version"] = 3.2
    cache["schema"] = "idx-full-quotes-v1"
    cache.setdefault("quotes", {})
    now = int(time.time())
    ok, failed = 0, []
    fallback_budget = args.fallback_limit

    for group in chunks(symbols, max(5, min(args.batch_size, 50))):
        returned = {}
        try:
            items = spark_batch(group)
            for item in items:
                raw_symbol = str(item.get("symbol") or "").replace(".JK", "")
                response = (item.get("response") or [None])[0]
                if raw_symbol:
                    returned[raw_symbol] = response
        except Exception as exc:
            print(f"BATCH FAIL {group[0]}..{group[-1]}: {exc}")

        for symbol in group:
            response = returned.get(symbol)
            quote = parse_response(symbol, response, now) if response else None
            if not quote and fallback_budget > 0:
                fallback_budget -= 1
                quote = parse_response(symbol, fallback_one(symbol), now)
            if quote:
                cache["quotes"][symbol] = quote
                ok += 1
            else:
                failed.append(symbol)
        time.sleep(0.18 + random.random() * 0.12)

    cache["updatedAt"] = now
    cache["refresh"] = {
        "tickerCount": len(symbols),
        "successCount": ok,
        "failedCount": len(failed),
        "failed": failed,
        "batchSize": args.batch_size,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(OUT)
    print(f"quotes: success={ok}/{len(symbols)} failed={len(failed)}")
    if ok == 0:
        raise SystemExit("No quotes refreshed")

if __name__ == "__main__":
    main()
