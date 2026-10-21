# Netpulse D1 Mini v1.1 — setup jaringan dan OTA

Firmware utama baru ada pada folder ini. Tampilan dan alur setup diadaptasi dari `attendance-2.0-offline.ino`, dengan API ESP8266 dan EEPROM menggantikan API ESP32/Preferences. Fitur absensi tidak disertakan.

## Upload pertama melalui USB

1. Buka `d1mini-netpulse.ino` dengan Arduino IDE. `setup_page.h` harus tetap di folder yang sama.
2. Pilih core ESP8266 3.1.2 dan board **LOLIN(WEMOS) D1 R2 & mini**. Flash 4 MB dengan ruang OTA (misalnya FS 1 MB, OTA sekitar 1 MB). Tidak membutuhkan ArduinoJson atau config.h.
3. Upload melalui kabel USB, lalu buka Serial Monitor 115200 baud.
4. Password statis AP dan login admin adalah `waron114`. Password lama dimigrasikan saat boot tanpa menghapus pengaturan Wi-Fi/backend.

## Pengaturan jaringan

Saat belum memiliki SSID atau gagal tersambung selama 30 detik, Wi-Fi **NETPULSE-<chip-id>** tersedia. Sambungkan menggunakan password `waron114`. Buka **http://192.168.4.1/setup** secara manual (bukan captive portal otomatis).

Login: `admin`, password `waron114`. Halaman menyediakan scan Wi-Fi, daftar SSID terurut sinyal, SSID manual, password, dan **Simpan & Reconnect**. Scan hanya dijalankan saat diminta. Pilih jaringan terbuka bila memang tanpa password. Password kosong pada SSID yang sama mempertahankan password lama.

Setelah terhubung, AP setup ditutup dan Serial Monitor menampilkan alamat IP LAN baru. Buka `http://IP-WEMOS/setup`. Jika koneksi gagal, AP fallback muncul; perangkat terus mencoba jaringan tersimpan setiap sekitar 60 detik. Pengaturan jaringan disimpan pada EEPROM emulasi flash, hanya ketika tombol simpan ditekan.

## Pengaturan backend

Daftarkan lantai di komputer server:

```sh
python3 server.py --register-floor 1
python3 server.py --host 0.0.0.0 --port 8000
```

Isi halaman setup dengan alamat backend HTTP tanpa trailing slash, misalnya `http://192.168.1.10:8000`, ID `WMS-001` dan kunci hasil pendaftaran. Kunci tidak dikembalikan oleh API status; kolom kosong mempertahankan kunci lama. URL probe internet opsional harus HTTP eksternal dengan respons 204. Kosong berarti kualitas internet belum diketahui. HTTPS belum didukung firmware ini.

Telemetri setiap sekitar 10 detik, uji internet setiap sekitar 30 detik. Uji lokal menuju `/api/probe`; POST menuju `/api/telemetry`. Saat portal baru dibuka/scan berlangsung/upload aktif, pengujian dijeda sementara agar setup responsif. HTTP bersifat sinkron, jadi pada kondisi koneksi buruk respons portal dapat tertunda sampai request selesai. Jadwal bukan jaminan hard realtime. Offline pada dashboard berarti tidak ada telemetri selama 35 detik, bukan bukti internet mati.

## Upload firmware berikutnya melalui Wi-Fi

1. Compile/export binary untuk **ESP8266 D1 Mini** menggunakan layout flash yang sama. Jangan gunakan binary ESP32 dari file attendance atau image filesystem.
2. Buka `/setup`, login, lalu pilih `.bin` pada kartu **OTA Firmware Update**.
3. Klik Upload Firmware, konfirmasi, tunggu progress dan hasil verifikasi server. Progress 100% pengiriman belum berarti update berhasil.
4. Perangkat restart hanya setelah Update.end berhasil. Konfigurasi tetap tersimpan apabila layout/format EEPROM tidak berubah.

Upload dibatasi autentikasi Basic dan token sesi per boot. File harus `.bin` dengan header firmware, tersedia ruang OTA, dan lolos verifikasi updater. Ini bukan verifikasi tanda tangan digital atau jaminan bahwa binary ditujukan untuk aplikasi Netpulse; gunakan hasil build yang benar. Jika upload gagal/terputus, perangkat tidak sengaja direstart oleh handler; coba ulang atau pulihkan melalui USB. Jangan cabut daya selama penulisan flash.

Endpoint pada Wemos (berbeda dari endpoint backend):

| Endpoint | Fungsi |
| --- | --- |
| GET /setup | Halaman setup, login Basic |
| GET /api/status | Status, konfigurasi nonrahasia, token sesi; login Basic |
| GET /api/scan | Mulai/poll scan async; login Basic |
| POST /api/wifi | Form ssid, pass, open; login Basic + X-Device-Token |
| POST /api/config | Form backend, device, key, probe; login Basic + token |
| POST /api/ota | Multipart field file; login Basic + token |

API form menggunakan application/x-www-form-urlencoded. Tidak memakai JSON seperti versi attendance. Portal HTTP untuk LAN tepercaya; password dan kunci belum dienkripsi pada transport lokal. Jangan membuka port Wemos langsung ke internet.

## Pemisahan halaman status dan setup

- `http://IP-WEMOS/`: halaman status publik, hanya baca; diperbarui setiap 5 detik.
- `http://IP-WEMOS/health`: JSON status publik tanpa password/kunci/token.
- `http://IP-WEMOS/setup`: halaman pengaturan dan OTA, login `admin` / `waron114`.
- `/api/status` tetap khusus setup dan memerlukan login karena berisi token sesi.

Status internet menjadi belum diketahui jika Wi-Fi terputus atau uji terakhir lebih dari 60 detik. Membuka halaman status tidak menjeda telemetri.

## Ping dari dashboard (v1.10.0)

Panel dan endpoint ping lokal tidak digunakan. Pada dashboard utama, klik Ping di baris perangkat, isi IPv4 tujuan, lalu Mulai ping. Backend mengantrekan perintah untuk ESP; empat paket ICMP dijalankan ESP dan hasil otomatis dikirim tanpa menunggu interval telemetri normal. Maksimal tunggu 60 detik. Lihat docs/API.md untuk POST/GET `/api/v1/devices/{id}/ping`.

## Telemetri lengkap firmware 1.2.0

Firmware kini mengirim SSID/BSSID/kanal, IP/gateway/DNS, penghitung dan durasi disconnect sejak boot, kode alasan disconnect, uptime/memori/fragmentasi, alasan restart, kode HTTP/error pengujian, serta hasil ping manual terakhir. Kunci dan password tidak masuk body JSON. Detail kontrak: [API backend](../docs/API.md#telemetri-diperluas-firmware-120).

Setelah upload firmware 1.2.0, restart server.py agar migrasi database dan API baru aktif. Buka dashboard komputer server (`http://localhost:8000`) atau IP LAN server, bukan IP Wemos. Dalam sekitar satu siklus pengiriman dan refresh, kartu lantai memperlihatkan detail jaringan/perangkat. Klik **Lihat JSON telemetri** untuk memeriksa data terakhir.

Bagian dokumentasi ping sebelumnya yang menyebut hasil belum dikirim ke backend berlaku untuk firmware 1.1.0. Mulai versi 1.2.0 hasil terakhir ikut telemetri; pada versi lama pemicu ping melalui halaman status lokal Wemos. Mulai v1.10.0 gunakan tombol Ping pada dashboard utama.

## Mode Live ESP / Telemetri (v1.4.0)

Instal **ArduinoJson versi 7** dari Library Manager sebelum compile. Buka d1mini-netpulse.ino, pilih ESP8266 D1 Mini, lalu upload atau gunakan binary release v1.4.0 untuk OTA.

### Telemetri — pengaturan di ESP

Buka `http://IP-WEMOS/setup`, login admin/waron114. Pada Backend & Telemetri, isi **Interval kirim Telemetri** dan pilih **Detik** atau **Menit**, lalu Simpan Pengaturan. Rentang 10–3600 detik (1–60 menit untuk pilihan menit). Contoh: 30 Detik, 1 Menit, 5 Menit. Nilai tersimpan di EEPROM dan tetap berlaku setelah restart; tidak ditimpa pengaturan dashboard. Layout kredensial Wi-Fi lama dipertahankan.

### Live — permintaan dari dashboard pusat

Restart backend terbaru dan hard refresh browser. Pada Detail perangkat, pilih **Interval Live (detik)** (2–30), lalu **Aktifkan Live ESP**. Sesi berjalan maksimal **2 menit**, lalu kembali otomatis ke baseline Telemetri lokal. Tombol Hentikan Live mengembalikan lebih awal. Dashboard tidak mengubah interval Telemetri ESP.

ESP mengambil perintah tiap sekitar 5 detik. Status pending/dikonfirmasi muncul pada dashboard. Durasi dibatasi server dan lease lokal ESP; tutup tab tidak langsung menghentikan sesi. Kecepatan aktual tergantung HTTP/jaringan dan pengujian lain, bukan hard realtime.

Contoh: ESP diatur Telemetri 5 menit. Dashboard meminta Live setiap 3 detik. Selama sesi, ESP menargetkan kirim 3 detik; setelah maksimal 120 detik ia kembali ke 5 menit. Saat kembali, ESP mengirim laporan pergantian mode sekali agar backend mengetahui interval normal, kemudian mengikuti jadwal 5 menit.

[Kontrak API mode](../docs/API.md#mode-pengiriman-esp-firmware-140).

## Pemantauan layanan (v1.5.0)

Upload firmware v1.5.0 dan restart backend terbaru. Dashboard menampilkan Google, YouTube, TikTok dan Netflix; halaman perangkat menampilkan waktu DNS dan TCP serta umur pengujian. Uji adalah DNS + koneksi TCP 443, bukan streaming atau verifikasi HTTPS.

Target diuji bergiliran satu per siklus: interval antar target minimal 15 detik pada Telemetri, 3 detik pada Live. Jika interval Telemetri panjang, jalankan Live pada perangkat itu untuk memperoleh hasil keempat target lebih cepat. Target belum diuji ditandai belum diuji; hasil lama/offline ditandai data lama. Tidak ada pengujian yang dijalankan dari komputer backend sebagai pengganti ESP.

## Firmware 1.6.0 — tanpa probe internet terpisah

Pengaturan URL probe internet HTTP dihapus dari /setup dan request HTTP eksternal tidak dijalankan lagi. Internet diuji melalui empat layanan Google/YouTube/TikTok/Netflix yang sudah ada. `internet_ok` dan `internet_ms` sekarang melaporkan hasil uji layanan yang baru dilakukan pada siklus itu (DNS+TCP). Jika tidak melakukan uji baru, keduanya null. Detail tiap layanan tetap di `services`; internet_test_kind=dns_tcp dan service_target menunjukkan konteks hasil. `internet_http_code` null karena tidak ada pengujian HTTP internet.

GET `/api/probe` pada backend lokal **tetap digunakan** untuk grafik respons LAN dan bukan endpoint internet. Field probe lama di struktur EEPROM dipertahankan sebagai ruang cadangan agar update tidak menggeser/menghapus konfigurasi Wi-Fi dan kunci lama. Petunjuk pengisian probe pada bagian versi terdahulu tidak berlaku untuk v1.6.0.

## Tampilan portal lokal v1.7.0

Halaman status (`http://IP-ESP/`) dan pengaturan (`/setup`) memakai tampilan terang yang konsisten dengan dashboard: navigasi Status/Pengaturan, dua kolom pada desktop, satu kolom pada HP, kartu pengiriman data, alat ping, dan area upload firmware. Semua CSS/HTML disimpan dalam firmware tanpa font/CDN eksternal, sehingga tetap tampil saat internet tidak tersedia. Fungsi autentikasi, Wi-Fi, telemetri, dan OTA tetap menggunakan mekanisme yang sama. Halaman ini untuk ESP8266 D1 Mini pada proyek ini, bukan binary ESP32.

## Dua fitur pada satu D1 Mini (v1.9.0)

Di `/setup`, centang pemantauan jaringan, sensor suhu, atau keduanya. Sensor memakai DHT21 dengan DATA pada **D1 (GPIO5)**. Instal library DHT sensor library by Adafruit dan Adafruit Unified Sensor. Interval telemetri, jaringan dan OTA tetap memakai pengaturan yang sama. Lihat [panduan sensor](../docs/TEMPERATURE.md).

Firmware v1.10.0: panel Diagnostik Ping lokal dan route /api/ping dihapus. Gunakan tombol Ping pada dashboard utama; hasil diambil otomatis dari telemetri perangkat.

### Format terminal ping (v1.11.0)

Dashboard menampilkan setiap callback balasan/timeout dengan icmp_seq, bytes dan time, lalu packet loss serta RTT min/avg/max. Hasil tampil setelah pengujian selesai. TTL tidak tersedia pada SDK bawaan, waktu memiliki resolusi 1 ms. Upload waron-iot-d1mini-v1.11.0.bin dan restart backend agar field packets tersimpan.

### Logo perangkat (v1.12.0)

Header Status dan Pengaturan menggunakan logo.png asli dengan lebar responsif dan proporsi utuh. Gambar ditanam dalam logo_png.h dan dilayani ESP di /logo.png, sehingga tetap tampil tanpa koneksi backend/internet. Upload firmware v1.12.0 agar tampilan baru berlaku.

### Antarmuka mobile (v1.13.0)

Halaman Status dan Pengaturan memakai satu kolom dengan logo utuh, tab lebar, kartu ringkas, serta tombol/input 48 px. Angka suhu dan kelembapan lebih menonjol. Form memakai ukuran teks input 16 px agar nyaman pada ponsel. Tata letak melebar pada tablet/desktop; seluruh aset tetap dilayani lokal oleh ESP. Mekanisme koneksi, telemetri, sensor dan OTA tetap sama.

### Warna logo dan interaksi (v1.14.0)

Layout mobile tetap dipertahankan. Aksen menggunakan emas dan charcoal dari logo WARON, dengan latar netral hangat. Tombol, tab, fokus input, dan pesan status memiliki transisi singkat; pengaturan reduced motion dihormati. Scan dan simpan menampilkan teks proses lalu memulihkan label saat selesai. Status sukses/gagal tetap memakai warna semantik yang terpisah.

### Tema navy (v1.15.0)

Mengikuti referensi MyWaron: latar navy, kartu terang membulat, input abu-abu lembut, tombol biru dan logo asli pada bidang putih agar tetap terbaca. Layout mobile serta transisi singkat tetap dipakai. Tidak menambahkan form login baru.

### Optimasi akses lokal (v1.16.0)

HTML dikemas gzip dan dikirim langsung dari flash. Status sekitar 4,8 KB; Pengaturan sekitar 6,4 KB. Logo asli tetap utuh, memakai cache 7 hari dan ETag/304 agar tidak dikirim ulang jika browser sudah memilikinya. Kunjungan pertama masih perlu mengunduh logo sekitar 139 KB. Latar fixed dan animasi masuk halaman dihapus, serta bayangan diperingan. Permintaan UI diberi jeda 750 ms sebelum memulai pekerjaan jaringan berikutnya; server lokal dilayani lagi di antara polling kontrol dan telemetri. Pengujian DNS/TCP/HTTP masih sinkron, sehingga permintaan yang datang saat pengujian berlangsung tetap bisa menunggu timeout.

Setelah mengedit setup_page.h, status_page.h, atau logo.png, jalankan `python3 scripts/build_device_ui.py` **sebelum compile firmware**, agar ui_gzip.h diperbarui. File terkemas yang disertakan sudah cocok dengan v1.16.0.

### Identitas SVG ringan (v1.17.0)

Logo PNG diganti ikon SVG inline dan tulisan WARON-IoT pada Status/Pengaturan. Tidak ada permintaan /logo.png dari UI ESP dan gambar PNG tidak lagi ditanam di firmware. Setelah mengedit halaman, jalankan python3 scripts/build_device_ui.py sebelum compile. Dashboard utama, detail perangkat, dan suhu juga memakai ikon SVG dan tulisan yang sama.

### Wordmark tanpa ikon (v1.18.0)

Header Status/Pengaturan kini hanya menampilkan tulisan WARON-IoT berukuran 32 px (28 px pada layar sangat kecil). Ikon pada header dihapus. Dashboard menggunakan wordmark 30 px, atau 27 px di ponsel.

### Pengukuran gateway (v1.19.0)

ESP mengirim satu paket ICMP ke gateway Wi-Fi sebelum telemetri jaringan, termasuk saat Live. Hasil gateway_ping terpisah dari HTTP lokal dan ping manual. Ping SDK berjalan asinkron dan web server tetap dilayani; gateway dan ping manual berbagi mesin SDK secara bergantian. Interval adalah target terbaik sesuai durasi pengujian, bukan jaminan hard realtime. Grafik backend tidak lagi memakai HTTP backend sebagai pengukuran gateway. Upload firmware baru dan restart backend untuk mulai memperoleh data gateway. Router yang tidak membalas ICMP akan tercatat sebagai ping gagal; hasil ini tidak sendirian membuktikan internet mati.

## Firmware 1.20.0

ESP selalu mengukur dan mengirim jaringan serta suhu/kelembapan DHT21 pada D1 (GPIO5). Pilihan fitur di `/setup` dihapus, termasuk pengaruh pilihan lama di EEPROM. `enabled` dan `features` di `config/devices.json` hanya mengatur pemantauan dashboard. Jika sensor tidak terpasang atau gagal dibaca, `environment.sensor_ok` bernilai `false` dan nilai suhu/kelembapan `null`.
