#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

app = FastAPI(title="IDX Yahoo Proxy", version="1.0")
JAKARTA = ZoneInfo("Asia/Jakarta")
TOKEN = os.environ.get("MARKET_PROXY_TOKEN", "")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def finite(v):
    return isinstance(v, (int, float)) and math.isfinite(v)


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def get_json(url: str):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def parse(symbol: str, response: dict | None):
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
        va = quote.get("volume") or []
        volume = va[i] if i < len(va) else 0
        dt = datetime.fromtimestamp(stamp).astimezone(JAKARTA)
        rows.append({
            "time": int(stamp),
            "day": dt.strftime("%Y-%m-%d"),
            "open": vals[0], "high": vals[1], "low": vals[2], "close": vals[3],
            "volume": volume if finite(volume) else 0,
        })
    if not rows:
        return None
    latest_day = rows[-1]["day"]
    session = [r for r in rows if r["day"] == latest_day]
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


def fetch_batch(symbols):
    joined = ",".join(f"{s}.JK" for s in symbols)
    params = urllib.parse.urlencode({"symbols": joined, "range": "5d", "interval": "5m"})
    last_error = None
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            payload = get_json(f"https://{host}/v7/finance/spark?{params}")
            spark = payload.get("spark") or {}
            if spark.get("error"):
                raise RuntimeError(str(spark["error"]))
            result = spark.get("result") or []
            if result:
                return result
        except Exception as exc:
            last_error = exc
    raise RuntimeError(str(last_error or "Yahoo returned no data"))


def auth(authorization: str | None):
    if not TOKEN:
        raise HTTPException(status_code=503, detail="MARKET_PROXY_TOKEN not configured")
    if authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
def health():
    return {"ok": True, "service": "idx-yahoo-proxy", "time": int(time.time())}


@app.get("/quotes")
def quotes(
    symbols: str = Query(..., description="Comma-separated IDX tickers without .JK"),
    authorization: str | None = Header(default=None),
):
    auth(authorization)
    tickers = list(dict.fromkeys(s.strip().upper().replace(".JK", "") for s in symbols.split(",") if s.strip()))
    if not tickers or len(tickers) > 120:
        raise HTTPException(status_code=400, detail="symbols must contain 1-120 tickers")

    out = {}
    failed = []
    for group in chunks(tickers, 20):
        try:
            items = fetch_batch(group)
            returned = {}
            for item in items:
                raw = str(item.get("symbol") or "").upper().replace(".JK", "")
                response = (item.get("response") or [None])[0]
                if raw:
                    returned[raw] = response
            for symbol in group:
                q = parse(symbol, returned.get(symbol))
                if q:
                    out[symbol] = q
                else:
                    failed.append(symbol)
        except Exception:
            failed.extend(group)
        time.sleep(0.5)

    return JSONResponse({
        "version": 1,
        "updatedAt": int(time.time()),
        "tickerCount": len(tickers),
        "successCount": len(out),
        "failedCount": len(failed),
        "failed": sorted(set(failed)),
        "quotes": out,
    })
