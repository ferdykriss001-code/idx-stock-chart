#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, math, random, time, urllib.parse, urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "tickers.json"
JAKARTA = ZoneInfo("Asia/Jakarta")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; IDX-Chart-Lab-Full/3.3)",
    "Accept": "application/json,text/plain,*/*",
}

def finite(v): return isinstance(v, (int, float)) and math.isfinite(v)

def all_tickers():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    return sorted(set(str(x).upper().strip() for x in (cfg.get("all") or []) if str(x).strip()))

def request_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as response:
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
        try:
            payload = request_json(f"https://{host}/v7/finance/spark?{params}")
            spark = payload.get("spark") or {}
            if spark.get("error"):
                raise RuntimeError(str(spark["error"]))
            return spark.get("result") or []
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError(" / ".join(errors[-2:]))

def fallback_one(symbol):
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            payload = request_json(f"https://{host}/v8/finance/chart/{symbol}.JK?range=1d&interval=5m&includePrePost=false")
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
    ap.add_argument("--batch-size", type=int, default=30)
    ap.add_argument("--fallback-limit", type=int, default=10)
    ap.add_argument("--shard-index", type=int, default=0)
    ap.add_argument("--shard-count", type=int, default=1)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    symbols = all_tickers()
    selected = [s for i, s in enumerate(symbols) if i % args.shard_count == args.shard_index]
    now = int(time.time())
    quotes = {}
    failed = []
    fallback_budget = args.fallback_limit

    for group in chunks(selected, max(5, min(args.batch_size, 40))):
        returned = {}
        try:
            items = spark_batch(group)
            for item in items:
                raw_symbol = str(item.get("symbol") or "").replace(".JK", "")
                response = (item.get("response") or [None])[0]
                if raw_symbol:
                    returned[raw_symbol] = response
        except Exception as exc:
            print(f"SHARD {args.shard_index} BATCH FAIL {group[0]}..{group[-1]}: {exc}")

        for symbol in group:
            response = returned.get(symbol)
            q = parse_response(symbol, response, now) if response else None
            if not q and fallback_budget > 0:
                fallback_budget -= 1
                q = parse_response(symbol, fallback_one(symbol), now)
            if q:
                quotes[symbol] = q
            else:
                failed.append(symbol)
        time.sleep(0.08 + random.random() * 0.08)

    payload = {
        "version": 3.3,
        "schema": "idx-full-quotes-shard-v1",
        "updatedAt": now,
        "shard": args.shard_index,
        "shardCount": args.shard_count,
        "tickerCount": len(selected),
        "successCount": len(quotes),
        "failedCount": len(failed),
        "failed": failed,
        "quotes": quotes,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"shard {args.shard_index}: success={len(quotes)}/{len(selected)} failed={len(failed)}")
    if not quotes:
        raise SystemExit("Shard produced no quotes")

if __name__ == "__main__":
    main()
