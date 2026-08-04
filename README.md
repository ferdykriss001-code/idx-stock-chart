# IDX Stock Chart Lab

Chart saham IDX dengan candle harian 2 tahun dan cache candle 5-menit untuk sesi berjalan.

## Cakupan saham

- **First liner:** `top50` pada `config/tickers.json` (proksi kapitalisasi pasar besar).
- **Second liner:** konstituen Kompas100 yang bukan Top50.
- **Third liner:** seluruh saham biasa IDX aktif lain dari direktori perusahaan tercatat IDX.
- Ticker lama dan tambahan tetap dipertahankan, termasuk `BKSL`, `KETR`, `PTBA`, `NETV`, `PTRO`, `ITMG`, dan `JGLE`.

`scripts/sync_idx_universe.py` menyegarkan daftar aktif setiap maksimal 18 jam. File universe hanya diganti setelah respons IDX yang valid, sehingga kegagalan sementara tidak menghapus daftar sebelumnya. Pengelompokan first/second/third liner di sini bersifat operasional aplikasi, bukan klasifikasi resmi IDX.

## Pembaruan data

- **Daily:** GitHub Actions menjalankan pembaruan candle 2 tahun setelah bursa tutup.
- **Intraday:** empat shard paralel mengambil candle Yahoo Finance 5-menit setiap 5 menit pada sesi reguler IDX dan memublikasikannya dalam satu commit atomik.
- Cache intraday hanya menyimpan satu sesi berjalan; chart tetap memakai cache harian untuk riwayat dan indikator.
- Workflow pemeriksaan terpisah akan gagal/merah bila data cache yang dapat dilayani berumur lebih dari **10 menit** saat sesi reguler.
- Kegagalan ticker individual tidak menimpa cache valid sebelumnya; status refresh menyebutkan ticker yang belum memperoleh candle valid dari sumber.

Lihat `UPLOAD-INSTRUCTIONS.txt` untuk pemasangan.
