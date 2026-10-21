# Audit ESP D1 Mini — firmware 1.20.0

Tanggal: 21 Oktober 2026. Audit kode firmware, kedua halaman web perangkat, integrasi penerimaan backend, dan sumber library yang terpasang (ESP8266 core 3.1.2, DHT). Tidak ada pengujian fisik, packet capture, uji padam listrik, atau soak test pada sesi ini. Firmware tidak diubah oleh audit ini. Keberhasilan compile tidak membuktikan ketahanan operasi.

## Temuan prioritas

### 1. Tinggi — pengiriman gagal menghilangkan pengukuran

Lokasi: `sendTelemetry()` pada `d1mini-netpulse.ino`, sekitar baris 705–815.

`firstSend=false`, `lastSend=now`, dan `gatewaySampleReady=false` ditetapkan sebelum POST diketahui berhasil. HTTP 401/400/503 dan gangguan koneksi hanya dicatat di `lastPost`. Tidak ada antrean payload, retry terpisah, acknowledgment, atau penyimpanan offline. Sampel sensor di antara pengiriman memang tidak diarsipkan. Ketika Wi-Fi putus, fungsi langsung kembali dan data selama putus tidak terkumpul sebagai riwayat.

Dampak: grafik kosong ketika backend bermasalah; hasil gateway dapat terganti sebelum pernah diterima. Pada interval satu jam, satu kegagalan POST bisa membuat tidak ada laporan selama hampir dua jam. Ini masalah keandalan yang terlihat langsung dari alur kode.

Perbaikan: simpan payload pending beserta identitas boot/sequence, retry dengan backoff dan jitter; backend perlu idempotensi sebelum retry agar respons hilang tidak menghasilkan duplikat. Bedakan interval pengukuran, pengiriman, dan retry. Jika butuh rekam saat offline, tetapkan batas antrean dan kebijakan pemakaian flash.

### 2. Tinggi — jadwal pemeriksaan layanan terikat interval kirim

Lokasi: `measureNextService()` dan pemanggilnya di `sendTelemetry()`.

Hanya satu layanan diuji pada setiap siklus kirim. Pemeriksaan 15 detik normal/3 detik Live adalah batas minimum, bukan scheduler mandiri. Dengan kirim 60 detik, layanan yang sama baru diuji sekitar setiap 4 menit; dengan kirim 60 menit, sekitar setiap 4 jam. Dengan Live 2 detik dan ambang layanan 3 detik, pemeriksaan cenderung baru terjadi pada siklus 4 detik ditambah waktu operasi. Empat situs bukan diukur serentak.

Dampak: Live tidak menjamin semua situs diperbarui setiap 2 detik; nilai dapat merupakan hasil beberapa menit sebelumnya. Hasil lama tetap ikut payload dengan age yang meningkat.

Perbaikan: scheduler layanan mandiri, tetap bergiliran untuk membatasi beban, dengan target kesegaran yang jelas. Pengiriman mengambil snapshot hasil terakhir dan mencantumkan waktu pengukuran.

### 3. Tinggi — UI dapat tertahan operasi jaringan sinkron

Lokasi: `pollDeliveryControl()`, `measureNextService()`, `getStatus()`, `sendTelemetry()`, `loop()`.

DNS memiliki timeout 2 detik; TCP layanan 1,5 detik; GET lokal dan POST masing-masing 2 detik; kontrol 1,5 detik. Sumber WiFiClient core mengonfirmasi timeout juga digunakan pada koneksi TCP. Total ini bukan batas waktu end-to-end yang pasti: resolusi hostname backend dan fase HTTP dapat menambah waktu. `web.handleClient()` tidak dilayani di tengah operasi blocking tersebut, meskipun stack Wi-Fi bisa tetap mendapat yield.

Dampak: portal terasa lambat terutama saat tujuan diblokir, internet putus, atau backend mati. UI 4 detik timeout bisa menyerah sementara firmware masih mengerjakan jaringan. Menonaktifkan watchdog bukan solusi.

Perbaikan: state machine pekerjaan kecil, deadline menyeluruh, batas timeout per fase dan prioritas portal; ukur waktu loop terburuk pada jaringan gagal sebelum menjanjikan Live 2 detik.

### 4. Tinggi, perlu reproduksi fisik — ping dapat mengunci pengiriman tanpa jalur pemulihan

Lokasi: `startGatewayPing()`, `remotePing()`, callback ping, dan guard `sendTelemetry()`.

`pingRunning`/`gatewayRunning` hanya selesai ketika callback SDK datang atau `ping_start()` langsung gagal. Tidak ada deadline aplikasi apabila callback tidak pernah selesai, misalnya pada kondisi perubahan koneksi yang tidak ditangani firmware. `sendTelemetry()` selalu kembali selama flag aktif. Scan, konfigurasi, dan OTA juga menolak selama ping. SDK header lokal tidak menyediakan API pembatalan ping.

Dampak potensial: perangkat masih menjawab status tetapi berhenti mengirim dan menolak pengaturan. Belum terbukti callback hilang pada hardware; ini ketiadaan mekanisme recovery yang terkonfirmasi, bukan klaim bahwa hang pasti terjadi.

Perbaikan: deadline ping dan recovery yang aman sesuai lifecycle SDK; jangan sekadar membersihkan flag lalu memakai ulang buffer callback yang mungkin masih aktif. Pilihan terakhir reboot terkontrol setelah diagnostik disimpan.

### 5. Tinggi — pemeriksaan OTA belum membuktikan integritas dan identitas image

Lokasi: `handleUpload()`, `Update.begin(maxSpace)`, `Update.end(true)`.

Pemeriksaan aplikasi: autentikasi, token, ekstensi .bin, byte pertama 0xE9, dan hasil Updater. Tidak ada panjang image yang diharapkan, digest yang dibandingkan dengan sumber tepercaya, signature verifier, ataupun identitas produk/versi. Core Updater yang terpasang menyesuaikan ukuran ke data yang diterima ketika `end(true)` dipakai. Jadi pemeriksaan byte/format tidak cukup untuk menjamin file milik produk ini atau lengkap sesuai artefak rilis. Jangan menganggap `end(true)` sendiri sebagai bug: pola ini dipakai ketika ukuran awal adalah maksimum ruang OTA.

Dampak: firmware ESP8266 lain dapat diterima; file rusak/terpotong yang lolos pemeriksaan perlu diuji, bukan diasumsikan pasti aman. Tidak ada rollback aplikasi yang diimplementasikan.

Perbaikan: manifest image, ukuran dan hash yang diverifikasi, signature bila membutuhkan autentisitas; uji upload terputus, image board lain, ruang minim, dan reboot setelah update. Hindari menulis ulang slot tanpa rencana pemulihan fisik.

### 6. Sedang — portal dapat menunda telemetry/Live

Lokasi: `lastPortal`, `lastUiRequest`, `uiQuiet()`, guard akhir `loop()`.

Membuka `/setup` atau `/api/status` menahan kontrol dan telemetry hingga 15 detik sejak permintaan terakhir; setiap health/root request menahan hingga 750 ms. UI status resmi meminta health setiap 5 detik dan biasanya masih memberi waktu untuk kirim, tetapi beberapa klien/frekuensi lebih rapat bisa terus memperpanjang penundaan. Live expiration berada di `pollDeliveryControl()`, sehingga penghentian mode juga tertunda bila fungsi tidak dijalankan.

Perbaikan: pisahkan pengecekan expiry dari polling, beri anggaran UI tanpa menahan seluruh pengukuran selama 15 detik; tambahkan batas starvation.

### 7. Sedang — pembacaan respons kontrol belum dibatasi dengan benar

Lokasi: `pollDeliveryControl()`, kondisi `http.getSize() <= 1024`, `http.getString()`.

Respons tanpa Content-Length dapat mempunyai ukuran -1, tetap lolos kondisi. `getString()` lalu mengalokasikan respons penuh dan JsonDocument ArduinoJson 7 berkembang dinamis. Header Content-Length kecil juga bukan pengganti pembatas pembacaan stream.

Dampak potensial: heap habis karena respons salah/proxy/error atau server yang tidak dipercaya. Tidak ditemukan eksploitasi pada backend normal.

Perbaikan: pembacaan bounded sampai maksimal byte, deadline, limit nesting dan validasi tipe semua field, cek error alokasi.

### 8. Sedang — status internet hanya mewakili situs terakhir dan batas kesegaran tetap

Lokasi: `lastInternetOk`, `handleHealth()`.

`lastInternetOk` berasal dari layanan terakhir; `fresh` selalu maksimal 60 detik. Bila satu situs berhasil dan situs lain gagal, status umum bisa terlihat berhasil sesuai uji terakhir. Pada interval kirim lebih dari 60 detik, status berubah unknown sebelum jadwal pengukuran berikutnya meskipun konfigurasi normal.

Perbaikan: status agregat empat situs, eksplisit belum diuji/sebagian gagal/data lama; kesegaran mengikuti scheduler pengukuran, bukan konstanta yang tidak berhubungan interval.

### 9. Sedang — identitas hasil lama tidak direset penuh ketika konfigurasi/Wi-Fi berubah

Lokasi: `handleConfig()`, `connectWifi()`, `pingAvailable`, `gatewayAvailable`, `gatewaySampleReady`.

Hasil layanan direset, tetapi hasil ping manual dan seluruh state gateway tidak selalu direset. Mengubah ID/backend dapat mengirim ulang hasil manual dari job server sebelumnya. Perubahan koneksi saat gateway callback berjalan belum memiliki mekanisme identitas generasi; hasil target gateway lama berpotensi terbawa dengan gateway Wi-Fi baru. Backend memvalidasi target gateway dan dapat menolak seluruh payload jika tidak cocok.

Perbaikan: generation ID untuk jaringan/konfigurasi; hanya publikasikan hasil yang berasal dari generasi aktif dan batasi masa simpan ping manual.

### 10. Sedang — validasi konfigurasi ESP belum selaras dengan backend

Lokasi: `httpURL()`, `handleConfig()`.

Kunci ESP menerima 1–96 karakter, konfigurasi backend mensyaratkan 8–96. URL hanya memeriksa prefix http://, beberapa whitespace, dan panjang: query, fragment, host/port tidak valid atau userinfo belum ditolak. Suffix endpoint ditambahkan sebagai string.

Dampak: pengaturan dapat berhasil disimpan tetapi semua request gagal/401 atau menuju path berbeda. `http.begin()` gagal pada telemetry tidak memperbarui `lastPost`, sehingga UI dapat mempertahankan kode keberhasilan lama.

Perbaikan: validasi base URL dan panjang kunci konsisten; laporkan kegagalan inisialisasi request serta waktu keberhasilan terakhir.

### 11. Sedang — statistik putus koneksi bisa kehilangan kejadian

Lokasi: `connectWifi()` dan callback disconnect.

`hadConnection=false` diset sebelum `WiFi.disconnect(false)`. Disconnect akibat reconnect manual dapat tidak dihitung sebagai outage. Durasi putus baru ditambah saat got-IP, sehingga outage yang sedang berlangsung tidak masuk nilai kumulatif yang dilaporkan. Reboot menghapus statistik RAM.

Perbaikan: definisikan outage versus reconnect terencana, reset hanya di transisi yang benar, laporkan durasi outage berjalan terpisah.

### 12. Sedang — belum ada budget heap dan batas payload eksplisit

Lokasi: `jsonString()`, rangkaian `String payload +=`, `HTTPClient`.

String panjang dibangun bertahap tanpa `reserve`, tanpa memeriksa alokasi, tanpa memastikan payload <=4096 byte (batas backend). Ping dan seluruh layanan menambah ukuran. Ukuran normal belum menunjukkan masalah; kemungkinan fragmentasi/OOM jangka panjang perlu soak test, bukan dinyatakan sebagai kebocoran yang sudah terbukti. Hasil compile sebelumnya: RAM statis sekitar 41%, instruction RAM sekitar 92%; ini bukan ukuran heap tersisa saat runtime.

Perbaikan: penganggaran ukuran payload maksimum, serialisasi terukur/reserve, batas minimum free heap dan largest free block, monitoring alokasi serta uji OTA saat heap rendah.

### 13. Rendah–sedang — rollover millis dan akurasi timestamp

Lokasi: `uptime_s = millis()/1000`, penghitung outage, age hasil, penjadwalan.

Mayoritas interval menggunakan pengurangan unsigned yang tepat untuk wrap; jangan menyatakan semua timer rusak. Namun uptime yang ditampilkan kembali nol setelah millis 32-bit melingkar (sekitar 49,7 hari), penghitung durasi kumulatif juga dapat melingkar. Pembacaan sensor memakai usia, bukan waktu absolut, sehingga backend mengasumsikan waktu terima minus age sebagai waktu ukur; delay POST belum dihitung jika payload sudah terbentuk.

Perbaikan: uptime 64-bit dan dokumentasi timestamp; uji waktu dekat rollover dengan clock tiruan.

### 14. Sedang — batas keamanan operasional belum memadai untuk jaringan tak tepercaya

Lokasi: Basic Auth portal, SETUP_PASSWORD, httpURL(), endpoint /health, OTA.

Password statis dan HTTP saat ini merupakan keputusan pengguna; audit tidak mengubahnya. Konsekuensinya, orang yang mengetahui password dan dapat menjangkau perangkat bisa login/mengubah konfigurasi/upload firmware. Token tambahan melindungi aksi tulis dari sebagian skenario browser, tetapi bukan enkripsi. Kredensial backend dan portal lewat HTTP tidak terlindungi di transport. /health memang publik dan menampilkan SSID/IP/identitas/status.

Perbaikan bila cakupan akses diperluas: pembatasan jaringan, kredensial per perangkat, autentikasi firmware, dan transport aman yang diuji terhadap keterbatasan RAM.

## Hal yang bukan bug

- DHT21 membaca temperature lalu humidity: library DHT yang terpasang memakai cache pembacaan minimum 2 detik; dua pemanggilan berurutan tidak otomatis berarti dua transaksi sensor.
- Sensor tidak terpasang mengirim sensor_ok=false dan dua nilai null; format tersebut diterima validasi backend. Jangan kirim nol sebagai suhu pengganti.
- DNS + TCP 443 menguji resolusi dan pembukaan koneksi. Itu tidak membuktikan streaming, login, TLS, HTTP konten, kecepatan unduh, atau pengalaman semua pengguna lantai.
- Satu paket gateway per laporan cukup sebagai sampel RTT, tetapi loss 0/100% per sampel bukan estimasi packet loss yang stabil.
- Gzip halaman dan password tidak ikut endpoint health adalah perbaikan yang sudah ada.

## Urutan penanganan

1. Pisahkan scheduler pengukuran/pengiriman; buat pending payload + idempotensi backend dan retry.
2. Tambah deadline/recovery ping dan batas pekerjaan jaringan agar portal tetap responsif.
3. Perbaiki reset state saat konfigurasi/Wi-Fi berubah, response limit, status error dan kesegaran.
4. Perkuat OTA dan validasi pengaturan.
5. Uji perangkat fisik: backend mati/401/503, DNS blackhole, TCP drop, gateway tanpa ICMP, Wi-Fi reconnect saat ping, tanpa DHT/sensor lepas, upload terputus, konfigurasi saat Live, rollout 100 perangkat, soak 72 jam dan simulasi rollover.

Kriteria uji: tidak ada pengiriman berhenti permanen; antrean/drop terukur; tidak ada nilai sensor palsu; hasil gateway tidak salah target; Live berhenti sesuai lease lokal; portal memiliki batas latensi yang disepakati; hasil retry tidak menggandakan riwayat; image invalid tidak dilaporkan berhasil.
