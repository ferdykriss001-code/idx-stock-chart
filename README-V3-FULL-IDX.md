# IDX Chart Lab V3 Full IDX

Versi Full IDX menggunakan arsitektur yang lebih scalable daripada satu `market.json` besar.

## Universe saham

- Bootstrap paket: **881 ticker**
- Setelah workflow **V3 Full IDX - Sync ticker universe** berhasil, `config/tickers.json`
  akan disinkronkan dari halaman Company Profiles resmi IDX.
- Sinkronisasi memiliki pengaman: daftar lama tidak akan ditimpa jika scraper resmi
  hanya berhasil membaca kurang dari 900 ticker.
- `JGLE` tetap dipertahankan sebagai ticker tambahan/legacy.

## Data

- `config/tickers.json` — seluruh universe + Top 50 + legacy.
- `data/quotes.json` — snapshot intraday 5 menit untuk seluruh universe.
- `data/daily/SYMBOL.json` — histori daily 2 tahun per ticker.
- `data/market.json` — hanya fallback migrasi V3 lama; setelah Full IDX stabil boleh dihapus.

## Workflow yang harus dijalankan pertama kali

1. **V3 Full IDX - Sync ticker universe**
2. **V3 Full IDX - Refresh daily candles**
3. **V3 Full IDX - Refresh 5-minute quotes**

Daily candles memakai 12 shard paralel agar ratusan saham tidak diunduh oleh satu job.
Quote intraday memakai Yahoo Spark secara batch agar jumlah request jauh lebih kecil
daripada meminta endpoint chart satu per satu.

## Catatan target 5 menit

Cron GitHub meminta refresh setiap 5 menit, tetapi GitHub Actions tidak menjamin job
dieksekusi tepat pada menit tersebut. UI tetap menunjukkan umur data sebenarnya.
