#!/usr/bin/env python3
from __future__ import annotations
import json, os, time, urllib.parse, urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/'config'/'tickers.json'
OUT=ROOT/'data'/'quotes.json'
URL=os.environ.get('MARKET_PROXY_URL','').rstrip('/')
TOKEN=os.environ.get('MARKET_PROXY_TOKEN','')

if not URL or not TOKEN:
    raise SystemExit('MARKET_PROXY_URL and MARKET_PROXY_TOKEN are required')

cfg=json.loads(CONFIG.read_text(encoding='utf-8'))
symbols=list(dict.fromkeys(str(x).upper().strip().replace('.JK','') for x in (cfg.get('all') or []) if str(x).strip()))
if not symbols or len(symbols)>120:
    raise SystemExit(f'Expected 1-120 active tickers, got {len(symbols)}')

params=urllib.parse.urlencode({'symbols':','.join(symbols)})
req=urllib.request.Request(f'{URL}/quotes?{params}',headers={'Authorization':f'Bearer {TOKEN}','Accept':'application/json','User-Agent':'IDX-Chart-Lab-GitHub/1.0'})
with urllib.request.urlopen(req,timeout=120) as response:
    fresh=json.load(response)

fresh_quotes=fresh.get('quotes') or {}
if not fresh_quotes:
    raise SystemExit('Proxy returned zero quotes')
try:
    old=json.loads(OUT.read_text(encoding='utf-8'))
except Exception:
    old={'quotes':{}}
quotes=dict(old.get('quotes') or {})
quotes.update(fresh_quotes)
now=int(time.time())
payload={'version':5.0,'schema':'idx-active100-proxy-v1','updatedAt':now,'refresh':{'tickerCount':len(symbols),'successCount':len(fresh_quotes),'failedCount':len(symbols)-len(fresh_quotes),'failed':fresh.get('failed') or [],'source':'Yahoo Finance via private VPS proxy'},'quotes':quotes}
OUT.write_text(json.dumps(payload,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
print(f'PROXY SUCCESS {len(fresh_quotes)}/{len(symbols)}')
for s in ('DSSA','BKSL','KETR','BUMI'):
    q=quotes.get(s)
    if q: print(s,q.get('day'),q.get('close'))
