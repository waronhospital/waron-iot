# Akses controller ESP melalui dashboard

Halaman `/devices/WMS-001` menyediakan tombol **Akses controller**. Tombol membuka `/controllers/WMS-001/` pada server dashboard. Server meneruskan request ke IP Wi-Fi terakhir yang dilaporkan ESP pada port 80. Halaman status, setup, scan, simpan konfigurasi dan OTA menggunakan prefix proxy yang sama.

Server harus dapat menjangkau LAN ESP, termasuk dari container Docker. Ini reverse proxy HTTP melalui server, bukan tunnel outbound dari ESP. IP yang berubah baru diketahui setelah laporan berikutnya. Jika alamat terakhir sudah tidak aktif, proxy mengembalikan 502. Jangan meneruskan port ESP ke internet untuk memakai fitur ini.

`/setup` tetap meminta autentikasi ESP (admin dan password perangkat), lalu token sesi ESP untuk aksi tulis. Backend tidak menyimpan password admin atau melewati autentikasi. Status root tetap publik mengikuti firmware. Akses controller terbuka di origin dashboard seperti dashboard saat ini; fitur ini tidak menambahkan login server.

Hanya ID terdaftar/aktif, IP private IPv4 non-loopback dan rute portal firmware yang diperbolehkan. Tidak tersedia parameter URL tujuan bebas. Upload dibatasi 2 MiB. Firmware portal gzip didekompresi dan URL root ditulis ulang ke prefix perangkat. WebSocket atau firmware portal pihak lain belum didukung.

Jalankan ulang backend Python, atau rebuild Docker (`docker compose up -d --build`), setelah menambah modul proxy. Uji jaringan fisik ESP dan upload OTA tetap diperlukan; tes otomatis menggunakan respons HTTP tiruan.
