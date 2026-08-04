#!/usr/bin/env python3
"""Synchronize the active IDX equity universe used by IDX Chart Lab.

The IDX listed-company directory is the authoritative membership source. The
saved file is only replaced after a substantial, valid equity response so a
temporary upstream failure cannot erase the existing universe.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "tickers.json"
OUTPUT = ROOT / "config" / "idx-universe.json"
ENDPOINTS = (
    "https://www.idx.co.id/umbraco/Surface/ListedCompany/GetCompanyProfiles",
    "https://www.idx.co.id/primary/ListedCompany/GetCompanyProfiles",
)
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
    "Referer": "https://www.idx.co.id/en/companies/listed-companies/",
    "User-Agent": "Mozilla/5.0 (compatible; IDX-Chart-Lab-V2/2.1)",
}
MIN_VALID_TICKERS = 100


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def normalized_symbols(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    symbols: list[str] = []
    for item in values:
        symbol = str(item or "").upper().strip().removesuffix(".JK")
        if symbol and all(character.isalnum() for character in symbol):
            symbols.append(symbol)
    return list(dict.fromkeys(symbols))


def data_table_params() -> dict[str, str]:
    params: dict[str, str] = {
        "draw": "1",
        "start": "0",
        "length": "2000",
        "search[value]": "",
        "search[regex]": "false",
    }
    columns = ("KodeEmiten", "KodeEmiten", "NamaEmiten", "TanggalPencatatan")
    for index, column in enumerate(columns):
        prefix = f"columns[{index}]"
        params.update({
            f"{prefix}[data]": column,
            f"{prefix}[name]": "",
            f"{prefix}[searchable]": "true",
            f"{prefix}[orderable]": "false",
            f"{prefix}[search][value]": "",
            f"{prefix}[search][regex]": "false",
        })
    return params


def fetch_rows() -> tuple[list[dict[str, Any]], str]:
    query = urllib.parse.urlencode(data_table_params())
    errors: list[str] = []
    for endpoint in ENDPOINTS:
        try:
            request = urllib.request.Request(f"{endpoint}?{query}", headers=HEADERS)
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = json.load(response)
            rows = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)], endpoint
            errors.append(f"{endpoint}: response has no data array")
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
    raise RuntimeError(" | ".join(errors))


def is_stock(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def string_value(row: dict[str, Any], key: str) -> str:
    return str(row.get(key) or "").strip()


def to_companies(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    companies: dict[str, dict[str, str]] = {}
    for row in rows:
        if not is_stock(row.get("EfekEmiten_Saham")):
            continue
        symbol = string_value(row, "KodeEmiten").upper()
        if not symbol or len(symbol) > 4 or not symbol.isalnum():
            continue
        companies[symbol] = {
            "symbol": symbol,
            "name": string_value(row, "NamaEmiten") or f"{symbol} • IDX",
            "board": string_value(row, "PapanPencatatan"),
            "sector": string_value(row, "Sektor"),
            "listedAt": string_value(row, "TanggalPencatatan"),
        }
    return [companies[symbol] for symbol in sorted(companies)]


def valid_universe(payload: Any) -> bool:
    return isinstance(payload, dict) and len(normalized_symbols(payload.get("tickers"))) >= MIN_VALID_TICKERS


def load_existing() -> dict[str, Any] | None:
    try:
        payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError):
        return None


def is_stale(payload: dict[str, Any] | None, stale_hours: float, force: bool) -> bool:
    if force or not valid_universe(payload):
        return True
    try:
        return time.time() - float(payload.get("updatedAt", 0)) >= stale_hours * 3600
    except (TypeError, ValueError):
        return True


def tier_memberships(active_tickers: list[str]) -> dict[str, list[str]]:
    configured = json.loads(CONFIG.read_text(encoding="utf-8"))
    active = set(active_tickers)
    first = [symbol for symbol in normalized_symbols(configured.get("top50")) if symbol in active]
    first_set = set(first)
    second = [
        symbol
        for symbol in normalized_symbols(configured.get("kompas100"))
        if symbol in active and symbol not in first_set
    ]
    protected = first_set | set(second)
    third = [symbol for symbol in active_tickers if symbol not in protected]
    return {
        "firstLiner": first,
        "secondLiner": second,
        "thirdLiner": third,
    }


def synchronize(stale_hours: float, force: bool) -> None:
    existing = load_existing()
    if not is_stale(existing, stale_hours, force):
        print("IDX universe is still fresh; no sync required.")
        return

    try:
        rows, endpoint = fetch_rows()
        companies = to_companies(rows)
        tickers = [company["symbol"] for company in companies]
        if len(tickers) < MIN_VALID_TICKERS:
            raise ValueError(f"only {len(tickers)} valid equity tickers returned")
    except Exception as exc:
        if valid_universe(existing):
            print(f"IDX universe sync failed; keeping previous valid file: {exc}")
            return
        raise SystemExit(f"IDX universe sync failed and no valid prior file exists: {exc}") from exc

    now = int(time.time())
    payload = {
        "version": 1,
        "updatedAt": now,
        "source": {
            "name": "IDX Listed Company Directory",
            "url": endpoint,
            "retrievedAt": now,
        },
        "tickerCount": len(tickers),
        "tickers": tickers,
        "companies": companies,
        "tiers": tier_memberships(tickers),
        "tierDefinitions": {
            "firstLiner": "Top50 pada config/tickers.json; proksi saham berkapitalisasi besar.",
            "secondLiner": "Konstituen Kompas100 selain Top50; proksi saham likuid lapis kedua.",
            "thirdLiner": "Saham biasa IDX aktif lain yang tidak berada pada dua kelompok di atas.",
            "note": "Kategori ini adalah pengelompokan operasional aplikasi, bukan klasifikasi resmi IDX.",
        },
    }
    atomic_json(OUTPUT, payload)
    print(f"Saved {len(tickers)} active IDX equity tickers from {endpoint}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--if-stale-hours", type=float, default=18)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    synchronize(max(0, args.if_stale_hours), args.force)


if __name__ == "__main__":
    main()
