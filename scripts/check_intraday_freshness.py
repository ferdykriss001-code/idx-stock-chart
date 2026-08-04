#!/usr/bin/env python3
"""Fail the health workflow when intraday cache publication exceeds its target."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, time as clock_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "tickers.json"
UNIVERSE = ROOT / "config" / "idx-universe.json"
JAKARTA = ZoneInfo("Asia/Jakarta")


def normalized_symbols(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    symbols: list[str] = []
    for item in values:
        symbol = str(item or "").upper().strip().removesuffix(".JK")
        if symbol and all(character.isalnum() for character in symbol):
            symbols.append(symbol)
    return list(dict.fromkeys(symbols))


def expected_tickers() -> list[str]:
    configured = json.loads(CONFIG.read_text(encoding="utf-8"))
    groups = ("top50", "kompas100", "legacy", "additional")
    symbols = [symbol for group in groups for symbol in normalized_symbols(configured.get(group, []))]
    try:
        universe = json.loads(UNIVERSE.read_text(encoding="utf-8"))
        discovered = normalized_symbols(universe.get("tickers", []))
        if len(discovered) >= 100:
            symbols.extend(discovered)
    except (OSError, ValueError, TypeError):
        pass
    return list(dict.fromkeys(symbols))


def market_is_open(now: datetime) -> bool:
    if now.weekday() > 4:
        return False
    current = now.timetz().replace(tzinfo=None)
    if now.weekday() == 4:
        sessions = (
            (clock_time(9, 15), clock_time(11, 30)),
            (clock_time(14, 15), clock_time(16, 2)),
        )
    else:
        sessions = (
            (clock_time(9, 15), clock_time(12, 0)),
            (clock_time(13, 45), clock_time(16, 2)),
        )
    return any(start <= current <= end for start, end in sessions)


def load_status() -> dict[str, Any]:
    path = ROOT / "data" / "refresh-status-intraday.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("refresh status is not an object")
    return payload


def check(max_delay_minutes: int, force: bool) -> None:
    now_local = datetime.now(JAKARTA)
    if not force and not market_is_open(now_local):
        print(f"Outside regular IDX session ({now_local.isoformat()}); freshness check skipped.")
        return

    now = int(time.time())
    allowed_seconds = max_delay_minutes * 60
    failures: list[str] = []
    unsupported: list[str] = []

    try:
        status = load_status()
    except Exception as exc:
        raise SystemExit(f"Cannot read intraday refresh status: {exc}") from exc

    try:
        status_age = now - int(status.get("updatedAt", 0))
    except (TypeError, ValueError):
        status_age = allowed_seconds + 1
    if status_age > allowed_seconds:
        failures.append(f"refresh status is {status_age // 60} minutes old")

    expected = set(expected_tickers())
    rows = {
        str(row.get("symbol") or ""): row
        for row in status.get("results", [])
        if isinstance(row, dict)
    }
    missing_rows = sorted(symbol for symbol in expected if symbol not in rows)
    if missing_rows:
        failures.append(f"{len(missing_rows)} expected ticker(s) absent from status")

    for symbol in sorted(expected & set(rows)):
        row = rows[symbol]
        if not row.get("success"):
            if row.get("oldCachePreserved"):
                old = row.get("oldCacheUpdatedAt")
                if not isinstance(old, (int, float)) or now - int(old) > allowed_seconds:
                    failures.append(f"{symbol} preserved a stale cache")
            else:
                # A ticker without a valid Yahoo candle is recorded separately:
                # it is unavailable, rather than falsely reported as a delayed update.
                unsupported.append(symbol)
            continue

        path = ROOT / "data" / "intraday" / f"{symbol}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            updated_at = int(payload.get("updatedAt", 0))
            age = now - updated_at
            if age > allowed_seconds:
                failures.append(f"{symbol} is {age // 60} minutes old")
        except Exception:
            failures.append(f"{symbol} has no readable intraday cache")

    print(
        f"Freshness target: {max_delay_minutes} minutes | "
        f"tracked: {len(expected)} | unsupported by source: {len(unsupported)}"
    )
    if unsupported:
        print("Source has no valid intraday candle for: " + ", ".join(unsupported[:20]))
    if failures:
        raise SystemExit("FRESHNESS BREACH: " + " | ".join(failures[:30]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-delay-minutes", type=int, default=10)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    check(max(1, args.max_delay_minutes), args.force)


if __name__ == "__main__":
    main()
