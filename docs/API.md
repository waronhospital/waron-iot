# API integrasi Netpulse v1

Base URL contoh: `http://192.168.1.10:8000`. Semua respons JSON; waktu Unix dalam **milidetik** pada API v1. Tidak ada dependency tambahan. Spesifikasi tersedia di `/api/openapi.json` dan `docs/openapi.json`.

## Akses aplikasi lain

Server/native app dapat mengakses langsung menggunakan HTTP. Untuk frontend pada origin lain, izinkan origin yang diperlukan sebelum menjalankan server:

```sh
NETPULSE_CORS_ORIGINS=http://localhost:3000,http://localhost:5173 python3 server.py --host 0.0.0.0
```

CORS bukan autentikasi. Bila perlu batasi API integrasi dengan environment `NETPULSE_READ_TOKEN`, lalu kirim `Authorization: Bearer TOKEN_ANDA`. Jangan gunakan kunci Wemos sebagai token pembaca. Jangan menaruh token rahasia di frontend publik. Variabel ini melindungi `/api/v1/*`; dashboard lokal dan `/api/dashboard` tetap terbuka untuk LAN tepercaya. Untuk membatasi seluruh akses gunakan autentikasi pada reverse proxy.

| Method | Endpoint | Fungsi |
| --- | --- | --- |
| GET | `/api/v1/devices` | Semua lantai/perangkat dengan status dan kualitas internet |
| GET | `/api/v1/devices/WMS-001/latest` | Kondisi perangkat tertentu |
| GET | `/api/v1/devices/WMS-001/history?after_id=0&limit=100` | Riwayat terurut ID, maksimal 1000 per halaman |
| GET | `/api/dashboard` | Data tampilan dashboard dan tren 5 menit |
| GET | `/api/probe` | Target pengujian jaringan lokal, bukan uji internet |
| POST | `/api/telemetry` | Pengiriman pengukuran oleh Wemos |
| GET | `/api/openapi.json` | Spesifikasi OpenAPI |

```sh
curl http://192.168.1.10:8000/api/v1/devices
curl -H 'Authorization: Bearer TOKEN_ANDA' http://192.168.1.10:8000/api/v1/devices/WMS-001/latest
curl 'http://192.168.1.10:8000/api/v1/devices/WMS-001/history?after_id=0&limit=100'
```

Contoh bentuk data perangkat (nilai hanya ilustrasi):

```json
{
  "id": "WMS-001",
  "floor": 1,
  "status": "online",
  "last_seen_ms": 1790000000000,
  "rssi": -57,
  "local_ms": 12,
  "internet": {
    "status": "normal",
    "success_percent": 100.0,
    "average_ms": 180.0,
    "tests": 10
  }
}
```

Daftar membungkus data tersebut dalam `{"server_time_ms": ..., "devices": [...]}`. Status perangkat: `waiting`, `online`, `offline`. RSSI/local_ms menjadi null bila offline; local_ms juga null jika uji lokal gagal. Status internet: `unknown`, `normal`, `slow`, `partial`, `unreachable`; dihitung dari pengujian 5 menit terakhir. Offline selalu membuat kualitas internet unknown. Ambang awal: keberhasilan <95% partial; rata-rata >800 ms slow. Hasil masih sementara jika sampel sedikit.

Riwayat berisi `items`, `next_after_id`, `has_more`. Setiap item: `id`, `device_id`, `received_at_ms`, `rssi`, `local_ms`, `internet_ok`, `internet_ms`. Gunakan next_after_id untuk request berikutnya; simpan cursor per perangkat. Jika has_more true, lanjutkan segera. Jika false, polling lagi setiap 5–10 detik memakai cursor terakhir. Riwayat dibaca dari file JSONL harian tanpa penghapusan otomatis. Null internet_ok berarti tidak melakukan pengujian, false berarti pengujian gagal.

Contoh browser (origin harus diizinkan bila berbeda):

```js
const response = await fetch('http://192.168.1.10:8000/api/v1/devices');
if (!response.ok) throw new Error(`HTTP ${response.status}`);
const { devices } = await response.json();
console.log(devices);
```

Halaman HTTPS tidak dapat mengakses API HTTP karena mixed content; gunakan HTTPS pada reverse proxy untuk integrasi HTTPS.

## Telemetri Wemos

Header: Content-Type application/json, X-Device-ID, X-Device-Key. Body:

```json
{"rssi":-57,"local_ms":12,"internet_ok":true,"internet_ms":180}
```

RSSI integer -120..0. Durasi 0..120000 ms atau null. internet_ok boolean/null. internet_ms wajib angka jika internet_ok true; selain itu harus null. Endpoint membalas 201 dengan `status` dan `received_at` (khusus endpoint lama ini, Unix **detik**). Timestamp memakai penerimaan server; tidak mendukung replay/offline backfill.

Kode: 400 payload/query invalid; 401 autentikasi salah; 404 endpoint/perangkat tidak ditemukan; 413 ukuran body di luar 1–4096 byte. Error JSON berbentuk `{"error":"pesan"}`.

## Telemetri diperluas (firmware 1.2.0)

Empat field lama tetap berlaku. Metadata berikut bersifat opsional, sehingga firmware lama masih diterima:

```json
{
  "rssi": -57,
  "local_ms": 12,
  "internet_ok": true,
  "internet_ms": 180,
  "firmware_version": "1.2.0",
  "boot_id": "aabbccdd1122334455667788",
  "sequence": 125,
  "wifi": {
    "ssid": "Office",
    "bssid": "AA:BB:CC:11:22:33",
    "channel": 6,
    "ip": "192.168.0.103",
    "gateway": "192.168.0.1",
    "dns": "192.168.0.1",
    "disconnect_count": 2,
    "disconnected_ms": 18500,
    "last_disconnect_reason": 200
  },
  "device_health": {
    "uptime_s": 3750,
    "free_heap_bytes": 28560,
    "heap_fragmentation_percent": 12,
    "reset_reason": "Power on"
  },
  "diagnostics": {
    "local_http_code": 200,
    "internet_http_code": 204
  },
  "ping": {
    "job_id": 1,
    "target": "192.168.0.1",
    "sent": 4,
    "received": 3,
    "loss_percent": 25,
    "min_ms": 2,
    "avg_ms": 4,
    "max_ms": 6,
    "age_ms": 1000
  }
}
```

Nilai di atas ilustrasi. Backend menyimpan metadata tervalidasi pada objek JSON `details`. Snapshot dashboard, daftar perangkat, latest, dan setiap item history menyertakan `details`. Data lama tanpa metadata memiliki details kosong. Field tidak dikenal diabaikan, sehingga password/kunci tidak dimasukkan ke metadata yang dibaca aplikasi.

`sequence` bertambah setiap upaya pengiriman dan direset bersama boot_id saat restart. Tidak ada deduplikasi atau offline backfill. Counter disconnect mencatat putus setelah pernah mendapatkan IP; reconnect terencana dari portal tidak dihitung. Durasi terputus dijumlahkan setelah koneksi pulih, berdasarkan millis, bukan jam kalender. Counter hanya berlaku dalam sesi boot ini, bukan total sepanjang umur perangkat; millis ESP mengalami wrap sekitar 49,7 hari.

`diagnostics.internet_http_code` null jika siklus ini tidak melakukan uji internet. Angka negatif adalah error HTTPClient, bukan status HTTP. DNS/TCP belum diukur terpisah.

`ping` tidak disertakan sebelum ada hasil ping manual selesai. Sesudahnya, hasil terakhir dilampirkan pada telemetri berikutnya dengan age_ms (umur hasil saat laporan dibuat). Identifikasi hasil unik dengan boot_id + job_id; jangan menghitung setiap laporan berulang sebagai ping baru. Jika tidak ada balasan, semua RTT null. Hasil ini historis, bukan status ping realtime atau penilaian seluruh jaringan.

Dashboard menyediakan informasi perangkat/jaringan dan tombol **Lihat JSON telemetri** per lantai. Snapshot lama diberi label jika perangkat offline. Modal JSON adalah salinan saat tombol ditekan; tutup dan buka kembali untuk data terbaru.

## Dashboard Live (Server-Sent Events)

GET `/api/events` membuka koneksi text/event-stream. Event `dashboard` berisi snapshot JSON yang sama dengan `/api/dashboard`. Snapshot awal dikirim segera, lalu setiap telemetri baru selesai disimpan. Pembaruan juga dikirim setiap sekitar 5 detik untuk mengevaluasi offline dan kedaluwarsa riwayat. EventSource mencoba reconnect otomatis; setelah reconnect server mengirim snapshot terbaru, bukan replay semua event yang terlewat. Endpoint mengikuti akses LAN dashboard, tanpa Bearer token.

Dashboard memakai Live secara default. Tombol Jeda Live menutup stream, Aktifkan Live menyambungkan ulang, dan Sinkronkan mengambil snapshot sekali tanpa memaksa ESP melakukan pengukuran. Detail perangkat yang sedang terbuka ikut diperbarui. Jika menggunakan reverse proxy, nonaktifkan buffering untuk /api/events dan gunakan timeout koneksi panjang. Interval pengukuran ESP tetap sekitar 10 detik/telemetri dan 30 detik/uji internet: Live mengurangi jeda server-ke-browser, bukan menjamin pengukuran instan atau waktu nyata deterministik.

## Mode pengiriman ESP (firmware 1.4.0)

**Telemetri normal diatur pada ESP**, bukan dashboard: buka `/setup`, pilih interval dan satuan detik/menit (10 detik sampai 60 menit), simpan. Pengaturan tersimpan di EEPROM dan bertahan setelah restart, terpisah dari struktur Wi-Fi lama agar update tidak menghapus kredensial. Uji internet dilakukan saat siklus pengiriman, dengan jarak minimal 30 detik; interval kirim panjang membuat pengujian juga lebih jarang.

**Dashboard hanya meminta Live sementara**, dengan interval 2–30 detik dan batas 2 menit. Setelah selesai/lease habis, ESP memakai interval Telemetri lokal. Backend tidak boleh mengganti interval tersebut. Live berbeda dari stream SSE tampilan dashboard; menjeda tampilan tidak menghentikan sesi Live ESP. Tutup tab juga tidak langsung menghentikan sesi; tekan Hentikan Live atau tunggu kedaluwarsa.

### API kontrol

POST `/api/v1/devices/WMS-001/control`, Content-Type application/json:

```json
{"mode":"live","live_interval_s":5,"live_duration_s":120}
```

Berarti minta ESP mengirim sekitar setiap 5 detik selama maksimal 120 detik. Backend menolak durasi >120 detik dan interval di luar 2–30 detik. UI selalu meminta sesi 120 detik. API boleh meminta durasi lebih singkat.

Hentikan Live:

```json
{"mode":"telemetry"}
```

Field telemetry_interval_s pada POST kontrol ditolak: perubahan interval normal hanya di `/setup` ESP. Akses POST memakai Bearer NETPULSE_READ_TOKEN jika diset; kolom token tersedia pada dashboard. Server tanpa token adalah mode LAN tepercaya.

ESP mengambil GET `/api/device/control` setiap sekitar 5 detik menggunakan X-Device-ID dan X-Device-Key. Respons:

```json
{"mode":"live","telemetry_interval_s":60,"live_interval_s":5,"send_interval_s":5,"live_remaining_s":119,"revision":1}
```

telemetry_interval_s pada respons hanya informasi dari laporan ESP terakhir. Firmware 1.4.0 tidak menerapkannya ke pengaturan lokal; hanya live_interval_s dan lease yang diterapkan. Setelah reboot mode Live tidak diaktifkan sendiri, tetapi ESP dapat melanjutkan sisa sesi server yang masih berlaku. Jika server tidak dapat dihubungi, baseline Telemetri lokal tetap tersedia dan lease Live habis di ESP.

Objek delivery pada telemetri memuat mode aktual, send_interval_s, telemetry_interval_s lokal, dan revision kontrol terakhir. Dashboard membandingkan mode/revision/interval aktual untuk menampilkan status pending atau diterapkan. Menyimpan perintah ke server belum berarti ESP telah menjalankannya.

Kontrol/pengujian bersifat HTTP sinkron: delay jaringan, scan, ping, OTA dan portal setup dapat menunda pengambilan kontrol/pengiriman. Interval adalah target, bukan hard realtime. Jarak uji internet di Live minimal 10 detik; jika interval Live >10 detik, pengujian mengikuti siklus kirim.

Batas offline menggunakan `max(35, send_interval_s * 3 + 5)` berdasarkan interval aktual yang dilaporkan. Saat baseline 60 detik, offline setelah 185 detik. Grafik 5 menit dapat kosong bila interval normal lebih panjang. Metadata terbaru tetap tersedia dengan timestamp; kualitas internet menjadi belum diketahui jika tidak ada uji dalam jendela 5 menit.

## Google, YouTube, TikTok, Netflix — firmware 1.5.0

Uji dijalankan **dari ESP** ke empat hostname tetap: www.google.com, www.youtube.com, www.tiktok.com, www.netflix.com. Tahapnya resolusi DNS lalu membuka TCP 443 ke IP hasil resolusi. Koneksi langsung ditutup. Tidak ada TLS handshake, HTTP login, unduhan video, atau streaming. Status terjangkau hanya berarti DNS dan port TCP target dapat dijangkau; DNS sinkhole, proxy atau captive portal dapat membuat hasil berbeda dari pengalaman aplikasi asli. DNS bisa memakai cache; hasilnya bukan selalu waktu query DNS jaringan baru.

Telemetri opsional:

```json
{
  "services": {
    "google": {"dns_ok":true,"tcp_ok":true,"dns_ms":12,"tcp_ms":24,"age_ms":500},
    "youtube": {"dns_ok":false,"tcp_ok":false,"dns_ms":2000,"tcp_ms":null,"age_ms":1000}
  }
}
```

Hostname lain tidak dapat diperintahkan melalui payload; kunci layanan hanya google/youtube/tiktok/netflix. Layanan yang belum pernah diuji tidak disertakan. Hasil terakhir ikut laporan berikutnya, dengan umur hasil baru; jangan menganggap hasil yang diulang itu pengujian baru. Wi-Fi/backend dikonfigurasi ulang menghapus cache pengukuran dalam RAM ESP.

Setiap siklus telemetri menguji paling banyak satu target secara bergiliran. Pada mode normal jarak antar target minimal 15 detik; Live minimal 3 detik. Karena pengukuran mengikuti siklus pengiriman, interval normal 1 menit membutuhkan sekitar 4 menit untuk menguji semua target; interval 5 menit sekitar 20 menit. Gunakan sesi Live maksimal 2 menit untuk memeriksa semua target lebih cepat. Request DNS/TCP dapat memperpanjang siklus kirim, terutama saat gagal.

Backend menyimpan hasil dalam details.services dan menyediakan status turunan di node.services (dashboard) atau devices[].services (API v1). Status: reachable, dns_failed, tcp_failed, unknown, stale. Umur hasil dihitung dari age_ms saat laporan plus waktu sejak laporan diterima. Perangkat offline atau umur >max(90 detik, interval kirim * 5) menghasilkan stale, bukan mengulang status sukses lama sebagai kondisi saat ini. Belum pernah diuji menghasilkan unknown. Nilai tcp_ms pada kegagalan TCP adalah durasi upaya gagal, bukan RTT sukses.

## Grafik empat layanan

Snapshot `/api/dashboard` menyertakan `nodes[].service_history` dengan empat array google/youtube/tiktok/netflix. Setiap titik memuat time (Unix ms perkiraan waktu pengukuran), dns_ok, tcp_ok, dns_ms, tcp_ms. Waktu diperkirakan dari penerimaan laporan dikurangi age_ms. Pengulangan cache hasil sama dengan pergeseran waktu <=2 detik dihapus agar tidak dianggap pengukuran baru; pada firmware sekarang ini deduplikasi perkiraan karena belum ada ID uji layanan unik. Grafik memakai jendela 5 menit dan pilihan DNS, TCP, atau DNS+TCP. Kegagalan ditandai ×, bukan dianggap 0 ms. Halaman perangkat menampilkan hasil perangkat itu saja; dashboard utama merata-ratakan hasil berhasil per layanan per bucket 10 detik dari perangkat yang terpilih.

## Ringkasan dashboard utama 24 jam

GET `/api/overview` mengembalikan snapshot kondisi terakhir ditambah `summary_24h`: from_ms, to_ms, bucket_seconds=3600, local, services, hourly, slowest_hours. Setiap statistik berisi tests, failed, average_ms, p95_ms, success_percent. P95 adalah persentil nearest-rank pengukuran berhasil; kegagalan dihitung terpisah, bukan 0 ms. services memiliki total (DNS+TCP), dns, tcp. Riwayat hanya 24 jam terakhir; jam tanpa sampel menghasilkan jeda grafik, bukan data buatan. Waktu data berupa Unix ms, label grafik mengikuti zona waktu browser.

Backend menghitung dan menyimpan cache overview selama 300 detik. Halaman utama tidak membuka SSE dan mengambil overview sekali saat dibuka, lalu setiap 5 menit. Kondisi perangkat pada tabel dan strip adalah snapshot saat cache dibuat, bukan status realtime. Kartu layanan dan grafik memakai data historis 24 jam, sedangkan tabel mempertahankan kondisi/pengujian terbaru pada snapshot tersebut. Halaman `/devices/<ID>` tetap menggunakan SSE dan grafik jendela 5 menit.

Ringkasan per jam menggabungkan seluruh pengujian perangkat (setiap hasil berhasil memiliki bobot sama). ESP yang menjalankan Live menghasilkan lebih banyak sampel. Hasil layanan yang dilaporkan ulang dari cache dideduplikasi secara perkiraan seperti service_history. Jam paling lambat berarti jam dengan rata-rata respons tertinggi dari sampel yang ada, bukan kepastian jaringan selalu buruk pada jam itu.

## Firmware 1.6.0: hasil internet dari empat layanan

Probe HTTP internet terpisah dihapus. Empat field kompatibilitas tetap diterima: internet_ok dan internet_ms berasal dari hasil DNS+TCP layanan yang baru diuji pada siklus itu; null bila tidak ada uji baru. diagnostics.internet_test_kind=dns_tcp, diagnostics.service_target=google/youtube/tiktok/netflix (kosong jika tidak diuji), internet_http_code=null. Grafik/ringkasan empat layanan tetap memakai services yang sama; backend juga tetap menerima firmware lama dengan hasil HTTP probe. GET /api/probe lokal tetap dibutuhkan untuk uji backend/LAN.

## Ping dari dashboard / aplikasi lain

POST `/api/v1/devices/WMS-001/ping` dengan Content-Type application/json dan body `{"target":"192.168.0.1"}`. Jika server memakai NETPULSE_READ_TOKEN, sertakan Authorization: Bearer TOKEN (aturan akses sama dengan kontrol Live). Respons 202 berisi status queued, job_id, target. Hanya satu permintaan aktif per perangkat; tujuan IPv4 unicast, perangkat harus online dengan fitur jaringan aktif.

GET endpoint yang sama untuk status idle, queued, completed, atau expired. Saat completed, objek result berisi sent, received, loss_percent, min_ms, avg_ms, max_ms, target dan job_id. Perintah diambil ESP lewat `/api/device/control`, maksimal 60 detik untuk mengambil dan melaporkan hasil; empat paket ICMP dijalankan oleh ESP, bukan backend. Firmware v1.10.0 diperlukan. Dashboard hanya memeriksa hasil tiap 2 detik selama pengujian; interval ringkasan normal tetap berlaku. Tidak perlu password admin lokal ESP pada dashboard. UI dan endpoint ping lokal ESP dihapus.

### Hasil ping per paket (firmware v1.11.0)

Objek result menambahkan packets, array maksimal empat objek `{ "seq": 0, "bytes": 64, "ms": 4, "ok": true }`. Nilai berasal dari callback SDK ESP8266. Paket gagal ditampilkan sebagai Request timeout, tanpa memakai nilai bytes/ms-nya. TTL tidak diekspos SDK ini dan resolusi waktu 1 ms. Firmware lama tetap diterima, tetapi hanya menyediakan ringkasan. Dashboard menampilkan baris per paket setelah ESP selesai dan mengirim hasil; bukan streaming tiap balasan.

## Stream per perangkat

GET `/api/events?device_id=WMS-001` menghasilkan SSE dashboard untuk satu perangkat. GET `/api/dashboard?device_id=WMS-001` menyediakan snapshot terbatas yang sama. ID tidak ditemukan: 404. Stream mengirim event saat data/status berubah dan komentar keep-alive saat tidak berubah. Endpoint tanpa parameter tetap kompatibel untuk seluruh perangkat. Riwayat kini disimpan JSONL, sedangkan respons API tetap JSON biasa.

## Ringkasan per perangkat dan riwayat tanggal

/api/overview menambahkan summary_24h pada setiap node: statistik lokal, empat layanan dan slowest_hours selama 24 jam berjalan. Riwayat grafik per node dikosongkan agar respons dashboard utama lebih kecil.

GET `/api/dashboard?device_id=WMS-001&date=2026-10-20` mengembalikan view=history, date, timezone=Asia/Jakarta, jumlah samples, metadata laporan terakhir pada tanggal tersebut, serta summary_24h yang khusus mencakup tanggal itu (bukan rentang 24 jam berjalan). Statistik dan grafik dikelompokkan per jam. Tanggal harus YYYY-MM-DD dan tidak boleh di masa depan; tidak ada laporan menghasilkan ringkasan kosong, bukan data hari lain. Tanggal tanpa device_id atau digunakan pada SSE ditolak.

Tanpa date, endpoint dan SSE tetap realtime lima menit terakhir. Pemilihan tanggal tidak mengaktifkan Live ESP. Dashboard menutup stream saat membaca arsip tanggal lama; pemilihan hari ini membuka stream kembali, sedangkan tombol Live mempercepat pengiriman ESP maksimal dua menit dan menampilkan grafik lima menit terakhir.

### Tampilan hari ini otomatis

Halaman perangkat secara bawaan menampilkan tanggal hari ini WIB pada sumbu 00.00–24.00. SSE perangkat memberi tahu perubahan, lalu halaman mengambil ringkasan tanggal terbaru. Tanggal lama menutup stream dan menjadi arsip statis. Tombol Live otomatis mengubah grafik ke lima menit terakhir sekaligus meminta Live ESP; setelah dihentikan atau lease berakhir, tampilan kembali mengikuti hari ini. Tidak ada tombol Realtime terpisah.

Riwayat grafik lokal dan service_history menyertakan max_gap_ms per sampel, berdasarkan interval saat laporan tersebut diambil. Renderer mempertimbangkan interval sampel sebelum dan sesudahnya agar perpindahan Telemetri ke Live tidak memutus garis yang valid. Pengukuran gagal atau jeda di luar cadence tetap memutus garis; data tidak diisi atau disimulasikan.

## Ping gateway otomatis (firmware v1.19.0)

Field gateway_ping terpisah dari ping manual dan local_ms. gateway_ping berisi measurement_id, target (IPv4 gateway Wi-Fi), sent, received, loss_percent, min_ms, avg_ms, max_ms dan age_ms. Tidak ada balasan: RTT null. Firmware mengirim satu paket ICMP ke WiFi.gatewayIP() sebelum setiap laporan jaringan. Pengujian gateway dan ping manual memakai SDK bergantian; tidak dijalankan bersamaan. Mode suhu saja tidak melakukan ping gateway.

Snapshot perangkat menambahkan gateway_history lima menit terakhir. Ringkasan harian menambahkan gateway dan hourly[].gateway, dengan deduplikasi measurement_id/boot_id/waktu pengukuran. local_ms, history, summary.local tetap respons HTTP backend untuk kompatibilitas; grafik Ping ke gateway hanya membaca field gateway. Data lama tanpa gateway_ping tidak dianggap hasil gateway, termasuk ping manual ke tujuan lain.

### Daftar perangkat ringan

`GET /api/devices/status` mengembalikan snapshot perangkat jaringan tanpa riwayat grafik atau agregat 24 jam. Dashboard memuat endpoint ini dahulu, kemudian `/api/overview` untuk ringkasan. Telemetri dan format arsip tidak berubah. Pembacaan awal storage masih perlu memuat file laporan terakhir pada cache dingin.
