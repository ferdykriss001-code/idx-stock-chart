#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, math, time, urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
QUOTES = ROOT / 'data' / 'quotes.json'
DAILY_DIR = ROOT / 'data' / 'daily'
JAKARTA = ZoneInfo('Asia/Jakarta')
HEADERS = {'User-Agent':'Mozilla/5.0 (compatible; IDX-Chart-Lab-Manual/1.0)','Accept':'application/json,text/plain,*/*'}

def finite(v): return isinstance(v,(int,float)) and math.isfinite(v)

def get(symbol, interval, range_):
    last=None
    for host in ('query1.finance.yahoo.com','query2.finance.yahoo.com'):
        url=f'https://{host}/v8/finance/chart/{symbol}.JK?range={range_}&interval={interval}&events=history&includeAdjustedClose=true'
        try:
            req=urllib.request.Request(url,headers=HEADERS)
            with urllib.request.urlopen(req,timeout=30) as r: payload=json.load(r)
            result=((payload.get('chart') or {}).get('result') or [])
            if result: return result[0]
        except Exception as e: last=e
    raise RuntimeError(last or 'Yahoo returned no data')

def intraday(symbol):
    result=get(symbol,'5m','1d'); q=((result.get('indicators') or {}).get('quote') or [{}])[0]
    rows=[]
    for i,stamp in enumerate(result.get('timestamp') or []):
        vals=[]
        for k in ('open','high','low','close'):
            a=q.get(k) or []; vals.append(a[i] if i<len(a) else None)
        if not all(finite(v) for v in vals): continue
        va=q.get('volume') or []; vol=va[i] if i<len(va) else 0
        dt=datetime.fromtimestamp(stamp).astimezone(JAKARTA)
        rows.append({'time':stamp,'day':dt.strftime('%Y-%m-%d'),'open':vals[0],'high':vals[1],'low':vals[2],'close':vals[3],'volume':vol if finite(vol) else 0})
    if not rows: raise RuntimeError('No valid 5m candles')
    day=rows[-1]['day']; session=[r for r in rows if r['day']==day]; now=int(time.time())
    return {'symbol':symbol,'updatedAt':now,'latestCandleTime':session[-1]['time'],'day':day,'open':session[0]['open'],'high':max(r['high'] for r in session),'low':min(r['low'] for r in session),'close':session[-1]['close'],'volume':sum(r['volume'] or 0 for r in session),'bars':session}

def daily(symbol):
    result=get(symbol,'1d','2y'); q=((result.get('indicators') or {}).get('quote') or [{}])[0]; bars=[]
    for i,stamp in enumerate(result.get('timestamp') or []):
        vals=[]
        for k in ('open','high','low','close'):
            a=q.get(k) or []; vals.append(a[i] if i<len(a) else None)
        if not all(finite(v) for v in vals): continue
        va=q.get('volume') or []; vol=va[i] if i<len(va) else 0
        bars.append({'time':datetime.fromtimestamp(stamp).strftime('%Y-%m-%d'),'timestamp':stamp,'open':vals[0],'high':vals[1],'low':vals[2],'close':vals[3],'volume':vol if finite(vol) else 0})
    if len(bars)<5: raise RuntimeError('Not enough daily candles')
    meta=result.get('meta') or {}
    return {'version':3.4,'symbol':symbol,'updatedAt':int(time.time()),'source':'Yahoo Finance manual','latestCandle':bars[-1]['time'],'meta':{'longName':meta.get('longName') or meta.get('shortName') or f'{symbol} • IDX','exchangeName':meta.get('fullExchangeName') or 'IDX','regularMarketTime':meta.get('regularMarketTime'),'regularMarketPrice':meta.get('regularMarketPrice')},'bars':bars}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('symbol'); args=ap.parse_args(); symbol=args.symbol.upper().replace('.JK','').strip()
    cfg=json.loads((ROOT/'config'/'tickers.json').read_text(encoding='utf-8'))
    if symbol not in set(cfg.get('all') or []): raise SystemExit(f'{symbol} is not in IDX ticker universe')
    iq=intraday(symbol); dq=daily(symbol)
    try: book=json.loads(QUOTES.read_text(encoding='utf-8'))
    except Exception: book={'version':3.4,'schema':'idx-manual-quotes-v1','quotes':{}}
    book.setdefault('quotes',{})[symbol]=iq; book['updatedAt']=int(time.time()); book['version']=3.4; book['schema']='idx-manual-quotes-v1'; book['lastManualSymbol']=symbol
    QUOTES.write_text(json.dumps(book,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    DAILY_DIR.mkdir(parents=True,exist_ok=True); (DAILY_DIR/f'{symbol}.json').write_text(json.dumps(dq,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    print(f'UPDATED {symbol}: 5m={iq["day"]} {iq["close"]}, daily={dq["latestCandle"]}')
if __name__=='__main__': main()
