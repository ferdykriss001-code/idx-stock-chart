#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "data" / "quotes.json"


def load_old():
    try:
        data = json.loads(TARGET.read_text(encoding="utf-8"))
        return data if isinstance(data.get("quotes"), dict) else {"quotes": {}}
    except Exception:
        return {"quotes": {}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", required=True)
    args = ap.parse_args()
    source = Path(args.input_dir)
    old = load_old()
    merged = dict(old.get("quotes") or {})
    failed = []
    shard_stats = []
    newest = 0

    files = sorted(source.rglob("quotes-shard-*.json"))
    if not files:
        raise SystemExit("No shard files found")

    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        newest = max(newest, int(data.get("updatedAt") or 0))
        for symbol, quote in (data.get("quotes") or {}).items():
            previous = merged.get(symbol)
            if not previous or int(quote.get("updatedAt") or 0) >= int(previous.get("updatedAt") or 0):
                merged[symbol] = quote
        failed.extend(data.get("failed") or [])
        shard_stats.append({
            "shard": data.get("shard"),
            "tickerCount": data.get("tickerCount", 0),
            "successCount": data.get("successCount", 0),
            "failedCount": data.get("failedCount", 0),
        })

    payload = {
        "version": 3.3,
        "schema": "idx-full-quotes-v2",
        "updatedAt": newest or int(time.time()),
        "quotes": merged,
        "refresh": {
            "mode": "8-parallel-shards",
            "shardCount": len(files),
            "successThisRun": sum(s["successCount"] for s in shard_stats),
            "failedThisRun": sum(s["failedCount"] for s in shard_stats),
            "cachedTickerCount": len(merged),
            "failed": sorted(set(failed)),
            "shards": shard_stats,
        },
    }
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"merged {len(files)} shards; cache={len(merged)} success={payload['refresh']['successThisRun']}")

if __name__ == "__main__":
    main()
