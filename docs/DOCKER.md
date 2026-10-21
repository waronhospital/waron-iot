# Menjalankan WARON IoT dengan Docker

Prasyarat: Docker Engine/Desktop dan Docker Compose v2. Jalankan semua perintah dari direktori proyek.

## Mulai

Jalankan:

```sh
docker compose up -d --build
docker compose ps
docker compose logs --tail=50 -f
```

Volume konfigurasi baru otomatis diisi template. Jika sudah mempunyai `config/devices.json` yang benar, salin ke volume:

```sh
docker compose cp config/devices.json waron-iot:/app/config/devices.json
docker compose exec waron-iot python server.py --check-config
```

Dashboard: `http://localhost:8000`. Dari komputer/ESP lain gunakan IP LAN host Docker, misalnya `http://192.168.0.52:8000`. Masukkan alamat tersebut sebagai backend ESP; jangan memakai `localhost`, nama service Docker, atau IP internal container pada ESP. Izinkan akses port pada firewall host.

Jika backend Python lama masih berjalan di port 8000, hentikan proses tersebut atau gunakan port berbeda:

```sh
WARON_PORT=8001 docker compose up -d --build
```

Gunakan port yang sama setiap menjalankan Compose; lebih mudah simpan `WARON_PORT=8001` di `.env` (salin `.env.example` terlebih dahulu). ESP perlu menggunakan port baru juga. Jangan menjalankan dua backend yang menulis direktori data sama.

## Konfigurasi dan penyimpanan

- Konfigurasi memakai named volume `device-config`. Pada volume baru, Docker menyalin template `devices.json` dari image. Perubahan konfigurasi dibaca saat permintaan API berikutnya; refresh dashboard.
- Telemetri, `state.json`, kunci hash dan lock tersimpan di named volume `telemetry`, bukan di image. Compose memberi prefix nama proyek pada volume.
- Image hanya menyertakan template konfigurasi, bukan `config/devices.json` lokal, `.env`, firmware, atau riwayat lokal `data/`.
- Container berjalan sebagai UID/GID 10001, filesystem aplikasi read-only. `/app/data`, `/app/config`, dan `/tmp` tetap dapat ditulis.
- Timezone Asia/Jakarta; file harian mengikuti WIB.
- Healthcheck menguji HTTP backend, bukan akses internet ESP atau keberhasilan tulis storage.
- `restart: unless-stopped` memulai ulang saat proses berhenti; status unhealthy sendiri tidak otomatis me-restart container.

**Data lokal lama tidak otomatis masuk volume.** Instalasi pertama memakai penyimpanan baru. Untuk memindahkan riwayat dan identitas perangkat, hentikan backend Python lama dan container dahulu, kemudian salin seluruh isi data, termasuk state dan arsip:

```sh
docker compose create
docker compose stop
docker compose run --rm --no-deps --user 0 --entrypoint sh \
  -v "$PWD/data:/source:ro" waron-iot \
  -c 'cp -a /source/. /app/data/ && chown -R 10001:10001 /app/data'
docker compose up -d
```

Lakukan migrasi hanya ke volume kosong atau volume yang sudah dibackup; perintah salin dapat menimpa nama file yang sama. Jangan menjalankan lagi backend lama sebagai penulis. Startup mengurus migrasi arsip JSON lama sesuai mekanisme storage aplikasi.

## Backup dan pemulihan

Backup konsisten dilakukan ketika aplikasi berhenti:

```sh
mkdir -p backups
docker compose stop
docker compose run --rm --no-deps --user 0 --entrypoint sh \
  -v "$PWD/backups:/backup" waron-iot \
  -c 'tar -czf /backup/waron-data.tar.gz -C /app/data .'
docker compose up -d
```

Simpan `config/devices.json` secara terpisah bersama backup. Arsip mengandung konfigurasi/kunci sensitif. Salin arsip lama sebelum membuat backup baru dengan nama sama.

Pulihkan ke volume kosong saat aplikasi berhenti:

```sh
docker compose stop
docker compose run --rm --no-deps --user 0 --entrypoint sh \
  -v "$PWD/backups:/backup:ro" waron-iot \
  -c 'tar -xzf /backup/waron-data.tar.gz -C /app/data && chown -R 10001:10001 /app/data'
docker compose up -d
```

## Pembaruan dan pemeriksaan

```sh
docker compose up -d --build
docker compose exec waron-iot python server.py --check-config
curl http://localhost:8000/api/probe
curl http://localhost:8000/api/environment
```

`docker compose down` mempertahankan named volume. **`docker compose down -v` menghapus data volume.**

`NETPULSE_READ_TOKEN` dan `NETPULSE_CORS_ORIGINS` dapat diatur melalui `.env`. Token saat ini melindungi endpoint v1; tidak menjadikan seluruh dashboard/API privat. Dashboard belum menyediakan autentikasi token baca untuk semua alur, sehingga pengaturan token harus diuji terhadap pemakaian UI. Tidak diperlukan untuk LAN percobaan. Jangan mengekspos port ke internet tanpa pengaturan akses tambahan.

## Cakupan verifikasi

Tes Python dapat dijalankan dengan `python3 -m unittest discover -s tests`. Build image, healthcheck, izin volume, dan koneksi dari ESP perlu diverifikasi pada host yang menyediakan Docker. Docker tidak tersedia pada lingkungan implementasi saat paket ini disiapkan.

## Browser file telemetri

Compose menyediakan service `data-browser` pada `http://localhost:8081/` atau `http://IP-SERVER:8081/`. Jalankan `docker compose up -d --build`. Pilih berkas untuk membaca JSON/JSONL; simpan melalui browser untuk mengunduh. Port dapat diubah melalui `WARON_DATA_PORT` dalam `.env`.

Layanan ini tanpa login dan hanya-baca. Folder yang dibuka adalah `/app/data/telemetry` milik backend (volume sama), bukan seluruh data internal. State, lock, dan berkas setup tidak memiliki rute akses. Waktu daftar file Nginx mengikuti UTC; timestamp di dalam JSON mengikuti format data aplikasi. Sebelum laporan pertama, folder telemetry mungkin belum ada sehingga browser memberi 404; kirim telemetri atau impor riwayat dahulu.

Jika storage backend diganti ke bind mount `./data:/app/data`, ganti juga mount data-browser menjadi `./data:/srv/data:ro` agar kedua layanan membaca data sama. Jangan memakai volume berbeda.

Docker/Nginx tidak tersedia di lingkungan implementasi; konfigurasi perlu diverifikasi dengan `docker compose config` dan akses browser pada host Docker. Contoh pemeriksaan: `curl -I http://localhost:8081/` dan pastikan `curl -I http://localhost:8081/state.json` menghasilkan 404. Akses langsung tanpa login ini ditujukan bagi pihak yang bisa menjangkau port; isi telemetri memuat informasi jaringan seperti IP dan SSID.

## Volume konfigurasi

Compose memakai dua named volume: `device-config` untuk `/app/config`, dan `telemetry` untuk `/app/data`. Folder host `config/` tidak lagi di-mount. Volume baru diisi otomatis dengan template saat container dibuat; ID/kunci template perlu disesuaikan sebelum ESP dapat mengirim.

Untuk memakai konfigurasi lokal yang sudah benar:

```sh
docker compose up -d --build
docker compose cp config/devices.json waron-iot:/app/config/devices.json
docker compose exec waron-iot python server.py --check-config
```

Jangan salin konfigurasi lokal lagi jika konfigurasi volume sudah diedit dan ingin dipertahankan. Untuk mengedit via host: ekspor, edit file hasil ekspor, lalu salin kembali:

```sh
docker compose cp waron-iot:/app/config/devices.json ./devices.docker.json
# Edit devices.docker.json, kemudian:
docker compose cp ./devices.docker.json waron-iot:/app/config/devices.json
```

Untuk backup, salin konfigurasi dengan `docker compose cp waron-iot:/app/config/devices.json ./backups/devices.json` selain backup volume telemetri. Volume lama tidak ditimpa template ketika image dibangun ulang. `docker compose down -v` menghapus kedua volume, termasuk konfigurasi.
