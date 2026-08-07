#!/usr/bin/env python3
"""IDX Chart Lab V3 updater.

Writes ONE combined data/market.json file. Every ticker is refreshed independently;
when Yahoo fails for a ticker, its last valid cached data is preserved.
"""
from __future__ import annotations
import argparse, concurrent.futures, json, math, random, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/'config'/'tickers.json'
MARKET=ROOT/'data'/'market.json'
JAKARTA=ZoneInfo('Asia/Jakarta')
HEADERS={'User-Agent':'Mozilla/5.0 (compatible; IDX-Chart-Lab-V3/3.0)','Accept':'application/json,text/plain,*/*'}

def load_tickers():
    p=json.loads(CONFIG.read_text(encoding='utf-8'))
    seq=[]
    for key in ('top50','legacy','additional'):
        for x in p.get(key,[]):
            s=str(x).upper().strip()
            if s and s not in seq: seq.append(s)
    return seq

def finite(v): return isinstance(v,(int,float)) and math.isfinite(v)

def atomic_json(path,payload):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(payload,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    tmp.replace(path)

def load_market(tickers):
    try:
        m=json.loads(MARKET.read_text(encoding='utf-8'))
        if not isinstance(m.get('stocks'),dict): raise ValueError('bad market cache')
    except Exception:
        m={'version':3,'schema':'idx-chart-lab-market-v3','generatedAt':None,'tickers':tickers,'stocks':{},'refresh':{}}
    m['version']=3; m['schema']='idx-chart-lab-market-v3'; m['tickers']=tickers
    m.setdefault('stocks',{}); m.setdefault('refresh',{})
    for s in tickers:
        m['stocks'].setdefault(s,{'symbol':s,'meta':{},'daily':None,'intraday':None})
    return m

def url(symbol,mode,host):
    q='range=2y&interval=1d&events=history&includeAdjustedClose=true' if mode=='daily' else 'range=5d&interval=5m&events=history&includePrePost=false'
    return f'https://{host}/v8/finance/chart/{symbol}.JK?{q}'

def parse(symbol,mode,result):
    quote=((result.get('indicators') or {}).get('quote') or [{}])[0]
    ts=result.get('timestamp') or []
    bars=[]
    for i,stamp in enumerate(ts):
        vals=[]
        for k in ('open','high','low','close'):
            arr=quote.get(k) or []
            vals.append(arr[i] if i<len(arr) else None)
        if not all(finite(v) for v in vals): continue
        vols=quote.get('volume') or []; vol=vols[i] if i<len(vols) else 0
        row={'open':vals[0],'high':vals[1],'low':vals[2],'close':vals[3],'volume':vol if finite(vol) else 0}
        if mode=='daily':
            row.update({'time':datetime.fromtimestamp(stamp,timezone.utc).strftime('%Y-%m-%d'),'timestamp':stamp})
        else:
            dt=datetime.fromtimestamp(stamp,timezone.utc).astimezone(JAKARTA)
            row.update({'time':stamp,'day':dt.strftime('%Y-%m-%d'),'dateTime':dt.isoformat()})
        bars.append(row)
    dedup={b['time']:b for b in bars}; bars=list(dedup.values()); bars.sort(key=lambda b:b['time'])
    if not bars: raise ValueError('Yahoo returned no valid candles')
    meta=result.get('meta') or {}
    m={'longName':meta.get('longName') or meta.get('shortName') or f'{symbol} • IDX','exchangeName':meta.get('fullExchangeName') or 'IDX','regularMarketTime':meta.get('regularMarketTime'),'regularMarketPrice':meta.get('regularMarketPrice')}
    now=int(time.time())
    if mode=='daily':
        payload={'updatedAt':now,'source':'Yahoo Finance','latestCandle':bars[-1]['time'],'bars':bars}
    else:
        payload={'updatedAt':now,'source':'Yahoo Finance 5m','interval':'5m','latestCandleTime':bars[-1]['time'],'latestPrice':bars[-1]['close'],'bars':bars}
    return payload,m

def download(symbol,mode):
    last=None
    for attempt in range(4):
        for host in ('query1.finance.yahoo.com','query2.finance.yahoo.com'):
            try:
                req=urllib.request.Request(url(symbol,mode,host),headers=HEADERS)
                with urllib.request.urlopen(req,timeout=25) as r: payload=json.load(r)
                chart=payload.get('chart') or {}
                if chart.get('error'): raise RuntimeError(str(chart['error']))
                results=chart.get('result') or []
                if not results: raise ValueError('Yahoo returned no result')
                data,meta=parse(symbol,mode,results[0])
                return symbol,data,meta,None
            except Exception as e: last=e
        time.sleep(1.5 + attempt*2.0 + random.random()*1.2)
    return symbol,None,None,str(last or 'unknown error')

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--mode',choices=('daily','intraday'),required=True); ap.add_argument('--workers',type=int,default=6)
    a=ap.parse_args(); workers=max(1,min(a.workers,8))
    tickers=load_tickers(); market=load_market(tickers); started=int(time.time()); results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futs={pool.submit(download,s,a.mode):s for s in tickers}
        for f in concurrent.futures.as_completed(futs):
            symbol,data,meta,error=f.result(); entry=market['stocks'].setdefault(symbol,{'symbol':symbol,'meta':{},'daily':None,'intraday':None})
            if data:
                entry['symbol']=symbol; entry['meta']={**(entry.get('meta') or {}),**(meta or {})}; entry[a.mode]=data
                results.append({'symbol':symbol,'success':True,'candles':len(data['bars']),'latest':data['bars'][-1]['time']})
                print(f'OK {symbol}: {len(data["bars"])} {a.mode} candles')
            else:
                preserved=bool((entry.get(a.mode) or {}).get('bars'))
                results.append({'symbol':symbol,'success':False,'cachePreserved':preserved,'error':error})
                print(f'FAIL {symbol}: {error}; preserved={preserved}')
    results.sort(key=lambda x:x['symbol']); now=int(time.time()); ok=sum(1 for x in results if x['success'])
    market['generatedAt']=now
    market['refresh'][a.mode]={'startedAt':started,'updatedAt':now,'durationSeconds':now-started,'tickerCount':len(tickers),'successCount':ok,'failedCount':len(tickers)-ok,'results':results}
    atomic_json(MARKET,market)
    print(f'{a.mode}: success={ok}/{len(tickers)} duration={now-started}s')
    if ok==0: raise SystemExit('No ticker could be refreshed')
if __name__=='__main__': main()
