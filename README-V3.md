# IDX Chart Lab V3

V3 memakai satu file `data/market.json` untuk seluruh ticker. Tidak ada lagi ketergantungan UI pada puluhan file JSON per ticker.

## Fitur
- 50 saham market cap Jun 2026 + legacy + JGLE (57 ticker)
- Yahoo Finance daily 2 tahun
- Yahoo Finance 5-minute intraday
- Target schedule 5 menit saat hari kerja (GitHub Actions tidak menjamin tepat waktu)
- Retry 4x per ticker melalui query1/query2 Yahoo
- Data lama dipertahankan jika satu ticker gagal
- Satu commit `market.json` per refresh
- MA20/50/100/200, RSI, MACD, volume, support/resistance, gap, Fibonacci dengan toggle
- UI memberi warning bila intraday >15 menit

## Instalasi
Upload seluruh isi folder ini ke root branch `main`. Setelah commit, buka Actions dan jalankan **V3 Refresh IDX daily data**, kemudian **V3 Refresh IDX 5-minute data**.

## Penting
Candle 5 menit adalah interval candle. Yahoo Finance dan GitHub Actions dapat mengalami delay; workflow menargetkan refresh 5 menit tetapi tidak dapat menjamin latency absolut di bawah 15 menit.
