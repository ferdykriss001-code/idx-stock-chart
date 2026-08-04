#!/usr/bin/env python3
"""Merge concurrent intraday shard results into one cache status report."""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("expected JSON object")
    return payload


def collect_statuses(root: Path, shard_count: int) -> list[dict[str, Any]]:
    statuses: dict[int, dict[str, Any]] = {}
    for path in root.rglob("refresh-status-intraday-shard-*.json"):
        try:
            payload = load_json(path)
            index = int(payload["shardIndex"])
            if index in statuses:
                raise ValueError(f"duplicate shard status {index}")
            statuses[index] = payload
        except Exception as exc:
            raise SystemExit(f"Invalid shard status {path}: {exc}") from exc

    missing = [str(index) for index in range(shard_count) if index not in statuses]
    if missing:
        raise SystemExit(f"Missing intraday shard status(es): {', '.join(missing)}")
    return [statuses[index] for index in range(shard_count)]


def copy_candles(root: Path) -> set[str]:
    destination = ROOT / "data" / "intraday"
    destination.mkdir(parents=True, exist_ok=True)
    copied: set[str] = set()
    for source in root.rglob("*.json"):
        if source.parent.name != "intraday" or source.parent.parent.name != "data":
            continue
        target = destination / source.name
        shutil.copyfile(source, target)
        copied.add(source.stem)
    return copied


def existing_candles() -> set[str]:
    directory = ROOT / "data" / "intraday"
    return {path.stem for path in directory.glob("*.json") if path.is_file()}


def merge(statuses_dir: Path, shard_count: int, data_already_present: bool) -> None:
    statuses = collect_statuses(statuses_dir, shard_count)
    copied = existing_candles() if data_already_present else copy_candles(statuses_dir)

    results: list[dict[str, Any]] = []
    result_symbols: set[str] = set()
    successful: set[str] = set()
    for status in statuses:
        for result in status.get("results", []):
            if not isinstance(result, dict):
                raise SystemExit("Invalid ticker result in shard status")
            symbol = str(result.get("symbol") or "")
            if not symbol or symbol in result_symbols:
                raise SystemExit(f"Duplicate or empty ticker result: {symbol!r}")
            result_symbols.add(symbol)
            if result.get("success"):
                successful.add(symbol)
            results.append(result)

    missing_files = sorted(successful - copied)
    if missing_files:
        raise SystemExit(f"Successful shards omitted candle files: {', '.join(missing_files[:10])}")

    now = int(time.time())
    started_at = min(int(status.get("startedAt", now)) for status in statuses)
    results.sort(key=lambda row: row["symbol"])
    payload = {
        "version": 3,
        "mode": "intraday",
        "startedAt": started_at,
        "updatedAt": now,
        "durationSeconds": now - started_at,
        "universeTickerCount": max(int(status.get("universeTickerCount", 0)) for status in statuses),
        "tickerCount": len(results),
        "shardCount": shard_count,
        "successCount": len(successful),
        "failedCount": len(results) - len(successful),
        "filesMerged": len(successful),
        "shards": [
            {
                "index": int(status["shardIndex"]),
                "tickerCount": int(status.get("tickerCount", 0)),
                "successCount": int(status.get("successCount", 0)),
                "failedCount": int(status.get("failedCount", 0)),
                "durationSeconds": int(status.get("durationSeconds", 0)),
            }
            for status in statuses
        ],
        "results": results,
    }
    atomic_json(ROOT / "data" / "refresh-status-intraday.json", payload)
    print(
        f"Merged {len(successful)} candle files; "
        f"{payload['successCount']}/{payload['tickerCount']} tickers refreshed."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--statuses-dir", type=Path, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--data-already-present", action="store_true")
    args = parser.parse_args()
    merge(args.statuses_dir, args.shard_count, args.data_already_present)


if __name__ == "__main__":
    main()
