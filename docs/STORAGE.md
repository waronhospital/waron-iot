# Penyimpanan JSONL harian

Backend memakai file JSONL per perangkat per hari. Firmware tidak perlu diubah. API tetap mengembalikan JSON biasa.

## Struktur

```text
data/
  state.json
  telemetry/
    WMS-001-21-10-2026.jsonl
    WMS-001-22-10-2026.jsonl
    WMS-002-21-10-2026.jsonl
```

Tanggal memakai waktu penerimaan server dalam Asia/Jakarta (WIB). Setiap laporan ditambahkan sebagai satu baris JSON utuh di akhir file. Baris sebelumnya tidak ditulis ulang. Contoh satu baris (ilustrasi):

```json
{"id":1792554000000000,"device_id":"WMS-001","received":1792554000,"received_at":"2026-10-21T10:40:00+07:00","rssi":-60,"local_ms":12,"internet_ok":null,"internet_ms":null,"details":{"features":{"network":true,"temperature":false}}}
```

JSONL memang satu laporan per baris tanpa indentasi. Jangan membaca seluruh file dengan json.load; gunakan json.loads untuk setiap baris. Endpoint history tetap menyediakan objek JSON biasa dengan pagination. state.json tetap berindentasi dan menyimpan metadata perangkat, hash kunci, kontrol Live/ping dan penanda migrasi. config/devices.json tetap menjadi pengaturan yang Anda edit.

## Migrasi dan menjalankan

Hentikan backend lama, lalu:

```sh
python3 server.py --check-config
python3 server.py --host 0.0.0.0 --port 8000
```

Startup menyalin sampel file JSON harian lama ke JSONL sambil mempertahankan ID. File JSON asli tetap menjadi arsip dan tidak dihapus. Migrasi ulang hanya menambahkan ID yang belum ada; jika kedua format tersedia pada hari yang sama, pembacaan memakai JSONL sehingga riwayat tidak dihitung dua kali. Jangan menjalankan backend format lama bersamaan dengan backend JSONL baru.

ID baru berupa angka mikrodetik yang monoton. Gunakan next_after_id dari API, bukan menghitung ID berikutnya sendiri. Tidak ada penghapusan otomatis setelah 7 hari. Cadangkan folder data; salinan JSON lama juga tetap memakan ruang sampai Anda mengarsipkannya sendiri.

## Penulisan dan pemulihan

Kunci thread dan file mengatur penulisan bersamaan. Satu laporan ditulis ke ujung file lalu flush/fsync sebelum diterima. Sampel yang sudah tersimpan tidak ditulis ulang. Jika proses terputus di tengah baris terakhir, pembaca mengabaikan fragmen tersebut; sebelum penulisan berikutnya, fragmen disalin ke file .partial-* lalu ujung file diperbaiki. Baris valid sebelumnya tetap dipertahankan. Kerusakan pada baris yang sudah diakhiri newline menimbulkan error dan tidak diam-diam dihapus.

state.json tetap memakai penggantian file atomik karena bukan log telemetri. Jika telemetri gagal disimpan, API tidak menyatakan accepted. Penyimpanan memakai filesystem lokal macOS/Linux, bukan filesystem jaringan untuk beberapa server.

## Dashboard dan cache

- Halaman utama mengambil ringkasan 24 jam setiap 5 menit; ringkasan disimpan dalam cache backend selama 300 detik.
- Halaman detail memakai /api/events?device_id=WMS-001. Snapshot dan permintaan cadangannya hanya membaca perangkat yang dipilih.
- Data baru/status yang berubah dikirim melalui SSE. Saat tidak berubah, server mengirim komentar keep-alive; status offline tetap dievaluasi tiap sekitar 5 detik.
- Live tetap setiap 2 detik dengan batas 2 menit, hanya perangkat yang dipilih.
- Data terbaru tiap perangkat disimpan dalam cache memori. Pembacaan JSONL yang masih ada dalam cache hanya memuat tambahan baris. Cache dokumen harian dibatasi 16 file; riwayat di disk tetap menjadi sumber persisten.

/api/events tanpa device_id tetap tersedia untuk aplikasi lama yang membutuhkan seluruh perangkat. Endpoint /api/dashboard?device_id=... menyediakan snapshot perangkat dengan format respons yang sama. Parameter perangkat yang tidak terdaftar menghasilkan 404.
