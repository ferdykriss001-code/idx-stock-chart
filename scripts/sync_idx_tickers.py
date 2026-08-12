#!/usr/bin/env python3
from __future__ import annotations
import json, re, sys, time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config" / "tickers.json"
URLS = [
    "https://www.idx.id/en/listed-companies/company-profiles/",
    "https://block.idx.id/id/data-pasar/data-saham/daftar-saham/",
]
CODE_RE = re.compile(r"^[A-Z0-9]{4,6}$")

def load():
    return json.loads(CFG.read_text(encoding="utf-8"))

def save(cfg):
    tmp = CFG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CFG)

def normalize_code(text):
    text = (text or "").strip().upper()
    return text if CODE_RE.fullmatch(text) else None

def scrape_with_playwright(url):
    from playwright.sync_api import sync_playwright
    symbols, names = set(), {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url, wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(6000)

        # If a page-size selector exists, choose its largest numerical option.
        try:
            selects = page.locator("select")
            for i in range(selects.count()):
                sel = selects.nth(i)
                options = sel.locator("option")
                vals = []
                for j in range(options.count()):
                    value = options.nth(j).get_attribute("value")
                    label = options.nth(j).inner_text().strip()
                    for candidate in (value, label):
                        try:
                            n = int(str(candidate).strip())
                            if n >= 50:
                                vals.append((n, value))
                        except Exception:
                            pass
                if vals:
                    vals.sort(reverse=True)
                    try:
                        sel.select_option(value=vals[0][1])
                        page.wait_for_timeout(2500)
                    except Exception:
                        pass
        except Exception:
            pass

        seen_pages = set()
        for _ in range(140):
            rows = page.locator("table tbody tr")
            page_signature = []
            for i in range(rows.count()):
                cells = rows.nth(i).locator("td")
                texts = []
                for j in range(min(cells.count(), 4)):
                    try:
                        texts.append(cells.nth(j).inner_text().strip())
                    except Exception:
                        texts.append("")
                page_signature.append("|".join(texts))
                code = None
                name = None
                for j, value in enumerate(texts):
                    c = normalize_code(value)
                    if c:
                        code = c
                        if j + 1 < len(texts):
                            name = texts[j + 1].strip()
                        break
                if code:
                    symbols.add(code)
                    if name and len(name) > 2:
                        names.setdefault(code, name)

            sig = "\n".join(page_signature)
            if sig in seen_pages:
                break
            seen_pages.add(sig)

            # Try common "next" controls.
            candidates = [
                'button[aria-label*="next" i]',
                'a[aria-label*="next" i]',
                'button:has-text("Next")',
                'a:has-text("Next")',
                'button:has-text("Berikutnya")',
                'a:has-text("Berikutnya")',
                'li.next:not(.disabled) a',
                '.pagination .next:not(.disabled)',
            ]
            clicked = False
            for selector in candidates:
                try:
                    loc = page.locator(selector).last
                    if loc.count() and loc.is_visible() and loc.is_enabled():
                        before = sig
                        loc.click(timeout=5000)
                        page.wait_for_timeout(1200)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                break
        browser.close()
    return symbols, names

def main():
    cfg = load()
    existing = set(cfg.get("all") or [])
    best_symbols, best_names, best_url = set(), {}, None
    errors = []

    for url in URLS:
        try:
            symbols, names = scrape_with_playwright(url)
            print(f"{url}: {len(symbols)} symbols")
            if len(symbols) > len(best_symbols):
                best_symbols, best_names, best_url = symbols, names, url
            if len(symbols) >= 900:
                break
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            print(f"SYNC ERROR {url}: {exc}")

    # Safety: never shrink a healthy universe because the website changed.
    if len(best_symbols) < 900:
        cfg["lastSyncError"] = (
            f"Official IDX scrape returned only {len(best_symbols)} symbols; "
            f"kept existing {len(existing)} bootstrap/previous symbols."
        )
        cfg["lastSyncAttemptAt"] = datetime.now(timezone.utc).isoformat()
        if errors:
            cfg["lastSyncDetails"] = errors[-3:]
        save(cfg)
        print(cfg["lastSyncError"])
        return 0

    merged = best_symbols | set(cfg.get("legacy") or []) | set(cfg.get("additional") or [])
    cfg["all"] = sorted(merged)
    cfg["names"] = {**(cfg.get("names") or {}), **best_names}
    cfg["source"] = "Official IDX Company Profiles auto-sync"
    cfg["sourceUrl"] = best_url
    cfg["syncedAt"] = datetime.now(timezone.utc).isoformat()
    cfg["officialSyncCount"] = len(best_symbols)
    cfg["lastSyncError"] = None
    cfg["lastSyncAttemptAt"] = cfg["syncedAt"]
    save(cfg)
    print(f"Saved {len(merged)} total symbols ({len(best_symbols)} official IDX).")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
