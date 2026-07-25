# IDX Stock Chart – Paket lengkap siap upload

Paket ini memuat 50 saham kapitalisasi pasar terbesar Juni 2026 serta ticker lama: BKSL, KETR, PTBA, NETV, PTRO, dan ITMG (total 56 ticker).

## Upload
1. Ekstrak ZIP.
2. Upload **seluruh isi folder ini** ke root branch `main` repository GitHub.
3. Pilih **Commit directly to main**.
4. Workflow `Refresh IDX daily + intraday data` akan berjalan otomatis karena `index.html` berubah.
5. Tunggu Actions hijau dan GitHub Pages selesai deploy, lalu tekan Ctrl+Shift+R.

## Workflow
- `refresh-market-data.yml`: membangun/memperbarui candle harian 2 tahun dan intraday 5 menit untuk semua ticker dalam satu commit. Dapat dijalankan manual.
- `refresh-intraday-data.yml`: memperbarui candle 5 menit setiap 10 menit pada hari kerja. GitHub Actions dan Yahoo Finance dapat terlambat, jadi kurang dari 15 menit adalah target, bukan jaminan.

## Catatan
File data lama disertakan agar ticker lama tetap bisa dibuka sebelum workflow pertama selesai. Ticker baru akan dibuat otomatis oleh workflow. Hasil per ticker dapat dilihat di `data/refresh-status.json` setelah workflow selesai.


## Indicator checklist
Semua indikator dapat diaktifkan/nonaktifkan: MA20/50/100/200, support, resistance, gap, Fibonacci Retracement, MACD, RSI, volume, dan Volume MA20. Pilihan tersimpan otomatis di browser.
