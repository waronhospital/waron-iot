# WARON IoT

Dashboard lokal dengan backend Python dan file JSONL harian. Python 3.9+; tanpa instalasi paket.

## Jalankan

Daftarkan satu Wemos untuk setiap lantai yang digunakan:

```sh
python3 server.py --register-floor 1
python3 server.py --register-floor 2
python3 server.py --host 0.0.0.0 --port 8000
```

Pendaftaran menampilkan `device_id` dan `device_key` sekali. Simpan kunci untuk firmware masing-masing Wemos; state.json hanya menyimpan hash. Pendaftaran ulang ditolak agar kunci tidak tertimpa.

Buka http://localhost:8000 di komputer server atau http://IP-SERVER:8000 dari jaringan yang sama. Jangan membuka index.html langsung. Belum ada data sampai Wemos mengirim. Ringkasan utama diperbarui setiap 5 menit; halaman detail memakai aliran pembaruan dari server. Backend lokal ini belum menyediakan login dashboard/TLS; gunakan di LAN tepercaya, bukan langsung di internet.

## Kontrak firmware

Wemos mengirim setiap 10 detik ke `http://IP-SERVER:8000/api/telemetry`:

- Method: POST, Content-Type: application/json
- Header `X-Device-ID`: ID hasil pendaftaran
- Header `X-Device-Key`: kunci hasil pendaftaran

```json
{
  "rssi": -57,
  "local_ms": 12,
  "internet_ok": true,
  "internet_ms": 180
}
```

Payload di atas hanya contoh format; jangan kirim sebagai pengukuran nyata.

`local_ms` adalah durasi permintaan ke GET `/api/probe`; null jika gagal. `internet_ok` bernilai true/false untuk pengujian internet yang baru dilakukan; null bila siklus ini tidak menguji internet. `internet_ms` hanya diisi untuk hasil berhasil, selain itu null. Jangan mengirim ulang hasil internet lama pada setiap siklus karena akan menghitungnya berulang. Timestamp menggunakan waktu penerimaan server; pengiriman ulang/offline buffering belum didukung.

Respons 201 berarti diterima, 400 payload tidak valid, 401 ID/kunci salah. Firmware tersedia di `firmware/netpulse/netpulse.ino`; ikuti [panduan Wemos](docs/WEMOS.md).

## Perilaku

- Satu atau beberapa perangkat per lantai, penempatan di config/devices.json; ID dan lantai tidak dipercaya dari body telemetri.
- Belum mengirim: menunggu data; batas offline mengikuti interval kirim perangkat (minimal 35 detik); kualitas internet menjadi belum diketahui.
- Pengujian internet 5 menit terakhir: tidak ada hasil → belum diketahui; semua gagal → tidak terjangkau; keberhasilan <95% → sebagian gagal; respons rata-rata >800 ms → lambat; selain itu normal. Ini ambang awal, bukan diagnosis pasti.
- Tren lokal menggunakan pengukuran asli, dengan jeda untuk data gagal/terputus.
- Riwayat tersimpan per perangkat/hari WIB, misalnya `data/telemetry/WMS-001-21-10-2026.jsonl`. Tidak dihapus otomatis. Lihat [penyimpanan JSON](docs/STORAGE.md).
- Kegagalan API mengosongkan metrik agar status lama tidak dianggap masih berlaku.

## Pengujian

```sh
python3 -m unittest discover -s tests -v
```

## Dokumentasi integrasi

- [Setup, upload, dan pengiriman realtime Wemos](docs/WEMOS.md)
- [API aplikasi lain, autentikasi, CORS, dan pagination](docs/API.md)
- [Spesifikasi OpenAPI](docs/openapi.json)

## Firmware portal setup dan OTA

Gunakan [d1mini-netpulse](d1mini-netpulse/README.md) untuk versi dengan konfigurasi jaringan/backend melalui browser dan upload firmware OTA. Folder `firmware/netpulse` adalah contoh awal konfigurasi statis.

## Konfigurasi ruangan dan perangkat

Edit `config/devices.json`, jalankan `python3 server.py --check-config`, lalu restart backend. Satu lantai dapat memiliki beberapa ruangan dan ESP. [Panduan JSON](docs/DEVICES.md).

## Halaman web

- `index.html`: dashboard seluruh perangkat, URL `/`.
- `device.html`: halaman khusus perangkat, URL `/devices/<ID>`.
- `assets/dashboard.css` dan `assets/dashboard.js`: gaya dan perilaku bersama untuk kedua halaman.

## Sensor suhu / kelembapan D1 Mini

Modul `/temperature` memakai firmware utama `d1mini-netpulse/` + DHT21 pada pin D1 (GPIO5). Aktifkan jaringan saja, suhu saja, atau keduanya melalui `/setup`. Lihat [panduan sensor](docs/TEMPERATURE.md).


Dashboard utama menampilkan ringkasan 24 jam untuk setiap perangkat tanpa grafik. Detail perangkat secara bawaan menampilkan hari ini (WIB) selama 24 jam dan diperbarui saat data masuk. Pilih tanggal lain untuk arsip. Tombol Live otomatis beralih ke lima menit terakhir dan mempercepat ESP maksimal dua menit, lalu kembali ke hari ini.

Grafik layanan pada halaman perangkat menampilkan DNS dan TCP terpisah secara bawaan: warna menunjukkan situs, garis utuh/titik penuh untuk DNS dan garis putus-putus/titik kosong untuk TCP. Pilihan yang tersedia adalah DNS & TCP terpisah, DNS saja, dan TCP saja. Tooltip menjelaskan jenis pengukuran; penanda D×/T× membedakan kegagalan.

## Docker

Jalankan `docker compose up -d --build`, lalu buka `http://localhost:8000`. Konfigurasi mengikuti `config/devices.json`; data disimpan persisten dalam named volume. Panduan port, migrasi data lama, dan backup: [docs/DOCKER.md](docs/DOCKER.md).
