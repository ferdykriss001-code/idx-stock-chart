#!/usr/bin/env python3
from __future__ import annotations

import json, math, random, time, urllib.parse, urllib.request, urllib.error
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

def finite(v): return isinstance(v,(int,float)) and math.isfinite(v)

def active_tickers():
    cfg=json.loads(CONFIG.read_text(encoding="utf-8"))
    symbols=cfg.get("all") or cfg.get("active100") or []
    symbols=[str(x).upper().strip().replace(".JK","") for x in symbols if str(x).strip()]
    symbols=list(dict.fromkeys(symbols))
    if not symbols: raise RuntimeError("No active tickers")
    if len(symbols)>120: raise RuntimeError(f"Safety stop: {len(symbols)} tickers")
    return symbols

def chunks(items,size):
    for i in range(0,len(items),size): yield items[i:i+size]

def get_json(url,timeout=25):
    req=urllib.request.Request(url,headers=HEADERS)
    with urllib.request.urlopen(req,timeout=timeout) as r: return json.load(r)

def parse(symbol,response):
    if not response: return None
    ts=response.get("timestamp") or []
    q=((response.get("indicators") or {}).get("quote") or [{}])[0]
    rows=[]
    for i,stamp in enumerate(ts):
        vals=[]
        for key in ("open","high","low","close"):
            a=q.get(key) or []; vals.append(a[i] if i<len(a) else None)
        if not all(finite(v) for v in vals): continue
        va=q.get("volume") or []; vol=va[i] if i<len(va) else 0
        day=datetime.fromtimestamp(stamp).astimezone(JAKARTA).strftime("%Y-%m-%d")
        rows.append({"time":int(stamp),"day":day,"open":vals[0],"high":vals[1],"low":vals[2],"close":vals[3],"volume":vol if finite(vol) else 0})
    if not rows: return None
    day=rows[-1]["day"]; session=[r for r in rows if r["day"]==day]
    if not session: return None
    now=int(time.time())
    return {"symbol":symbol,"updatedAt":now,"latestCandleTime":session[-1]["time"],"day":day,"open":session[0]["open"],"high":max(r["high"] for r in session),"low":min(r["low"] for r in session),"close":session[-1]["close"],"volume":sum((r["volume"] or 0) for r in session),"bars":session}

def fetch_batch(symbols):
    joined=",".join(f"{s}.JK" for s in symbols)
    params=urllib.parse.urlencode({"symbols":joined,"range":"5d","interval":"5m"})
    errors=[]
    # Two hosts only; one retry cycle. Avoid long cooldown loops that hit workflow timeout.
    for attempt in range(2):
        for host in ("query1.finance.yahoo.com","query2.finance.yahoo.com"):
            try:
                payload=get_json(f"https://{host}/v7/finance/spark?{params}")
                spark=payload.get("spark") or {}
                if spark.get("error"): raise RuntimeError(str(spark["error"]))
                result=spark.get("result") or []
                if result: return result
                raise RuntimeError("empty spark result")
            except urllib.error.HTTPError as exc:
                errors.append(f"{host}: HTTP {exc.code}")
            except Exception as exc:
                errors.append(f"{host}: {exc}")
        if attempt==0:
            wait=25+random.randint(0,5)
            print(f"Batch {symbols[0]}..{symbols[-1]} retry after {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(" | ".join(errors[-4:]))

def load_old():
    try:
        old=json.loads(OUT.read_text(encoding="utf-8"))
        return old if isinstance(old.get("quotes"),dict) else {"quotes":{}}
    except Exception:
        return {"quotes":{}}

def main():
    symbols=active_tickers(); old=load_old(); quotes=dict(old.get("quotes") or {})
    successes={}; failures=set(symbols)
    print(f"Refreshing {len(symbols)} tickers as 2 batches of 50", flush=True)

    for no,group in enumerate(chunks(symbols,50),1):
        print(f"BATCH {no}: {group[0]}..{group[-1]}", flush=True)
        try:
            items=fetch_batch(group); returned={}
            for item in items:
                raw=str(item.get("symbol") or "").upper().replace(".JK","")
                response=(item.get("response") or [None])[0]
                if raw: returned[raw]=response
            for symbol in group:
                q=parse(symbol,returned.get(symbol))
                if q:
                    successes[symbol]=q; quotes[symbol]=q; failures.discard(symbol)
            print(f"BATCH {no} success: {sum(1 for s in group if s in successes)}/{len(group)}", flush=True)
        except Exception as exc:
            print(f"BATCH {no} FAILED: {exc}", flush=True)
        time.sleep(8)

    # Publish partial success rather than throwing away an entire run.
    if not successes:
        raise SystemExit("Yahoo returned zero fresh tickers; quotes.json unchanged")

    now=int(time.time()); latest_days={}
    for q in successes.values(): latest_days[q["day"]]=latest_days.get(q["day"],0)+1
    payload={"version":4.2,"schema":"idx-active100-quotes-v3","updatedAt":now,"refresh":{"tickerCount":len(symbols),"successCount":len(successes),"failedCount":len(failures),"failed":sorted(failures),"latestDays":latest_days,"source":"Yahoo Finance spark v7 batch 50, 5m, 5d"},"quotes":quotes}
    tmp=OUT.with_suffix(".tmp"); tmp.write_text(json.dumps(payload,ensure_ascii=False,separators=(",",":")),encoding="utf-8"); tmp.replace(OUT)
    print(f"SUCCESS {len(successes)}/{len(symbols)} latestDays={latest_days}", flush=True)
    for s in ("DSSA","BKSL","KETR","BUMI"):
        if s in quotes: print(f"{s}: {quotes[s].get('day')} {quotes[s].get('close')}", flush=True)

if __name__=="__main__": main()
