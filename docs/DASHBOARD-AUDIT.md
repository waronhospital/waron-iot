# Audit dashboard WARON IoT

Tanggal: 21 Oktober 2026. Berdasarkan pembacaan HTML, JavaScript, dan backend saat ini. Tidak mencakup pengujian visual browser, aksesibilitas dengan pembaca layar, load test, atau pengujian pengguna. Tidak ada perubahan implementasi dalam audit ini.

## Temuan yang perlu diperbaiki

1. **Tinggi — keterangan jam paling lambat gateway mengambil metrik yang salah.** `drawSummaryChart()` di `assets/dashboard.js` menggambar `hourly.gateway`, tetapi memilih `peaks.local` untuk teks `local-peak`. Grafik benar-benar ping gateway, sedangkan teks penjelas dapat berasal dari HTTP ke backend. Gunakan `peaks.gateway` dan uji dengan puncak HTTP/gateway berbeda.

2. **Tinggi — kegagalan API disamakan dengan tidak ada perangkat.** `assets/temperature.js` menghapus `data.devices` saat fetch gagal lalu merender keadaan kosong. Halaman detail memberi petunjuk menambahkan `kind: temperature`, padahal konfigurasi sekarang memakai features. Dashboard jaringan juga mengosongkan perangkat saat disconnected. Pisahkan loading, error, belum terdaftar, fitur dinonaktifkan, dan data lama; pertahankan snapshot terakhir dengan penanda belum terverifikasi.

3. **Sedang — status saat ini bercampur dengan sejarah 24 jam.** Kondisi ruangan memakai online/RSSI/respons lokal terbaru dan hasil layanan terbaru, sementara kolom internet/layanan mengambil agregat 24 jam. Satu kegagalan layanan dalam 24 jam menghasilkan simbol × meskipun akses terbaru sudah pulih. `internetQuality()` masih dapat mengatakan normal ketika sebagian kecil uji gagal karena ambang 95%. Ini dapat dijelaskan, tetapi pengguna perlu tahu bahwa indikator mengukur hal berbeda. Pisahkan kondisi terakhir, persentase keberhasilan, dan riwayat insiden.

4. **Sedang — status operasional utama tertahan cache.** Cache overview 300 detik serta polling browser 300 detik berarti online/offline/RSSI pada tabel bukan status seketika. Pada beberapa browser dengan waktu polling berbeda, umur snapshot dapat mendekati 10 menit tepat sebelum polling berikutnya. Ringkasan 24 jam boleh jarang dihitung; status terakhir sebaiknya ditampilkan terpisah dari agregat dan selalu mencantumkan usia snapshot. Sesuaikan frekuensi hanya untuk status ringan, sesuai keinginan pengguna agar tidak mengambil seluruh riwayat tiap beberapa detik.

5. **Sedang — detail suhu belum setara fungsional dengan detail jaringan.** Suhu hanya polling 30 detik dan jendela 24 jam terakhir, tanpa pilihan tanggal atau tombol Live. Request halaman satu sensor tetap mengambil seluruh perangkat dan agregatnya melalui `/api/environment`. Tambahkan query selected-device, tanggal WIB, dan kontrol Live yang benar-benar dikonfirmasi ESP bila dibutuhkan.

6. **Sedang — normal suhu berarti sensor terbaca, bukan suhu aman.** Tidak ada batas suhu/kelembapan per ruangan, hysteresis, atau durasi pelanggaran. Saat ini tabel sudah memberi catatan bahwa normal bukan batas ruangan; jangan menafsirkan sensor_ok sebagai kondisi lingkungan normal. Batas harus ditentukan pemilik ruangan, bukan angka generik yang dianggap berlaku untuk semua lokasi.

7. **Sedang — status Live perlu memisahkan permintaan dan penerapan.** Tombol/countdown berasal dari control backend; title membedakan ESP sudah menerapkan atau masih menunggu, tetapi perbedaan penting itu belum jelas di tampilan utama. `device_day()` mengembalikan control telemetry dan online=false sebagai data historis, sedangkan inspector juga membaca control untuk UI Live. Pisahkan state kontrol aktual dari snapshot historis; uji buka arsip saat ESP sedang Live, klik Live saat offline, dan dua tab mengendalikan perangkat sama.

8. **Sedang — jam grafik belum konsisten WIB.** Batas tanggal jaringan memakai +07:00 dan beberapa label memakai Asia/Jakarta, tetapi banyak tooltip, clock, serta seluruh grafik suhu menggunakan timezone browser. Pengguna di zona waktu lain dapat melihat sumbu atau tooltip berbeda dengan tanggal arsip. Jadikan timezone eksplisit dan tampilkan WIB di semua konteks historis.

9. **Sedang — keterangan puncak layanan tidak mengikuti pilihan metrik.** Pemilihan DNS atau TCP mengubah seri grafik, tetapi teks jam paling lambat tetap berasal dari `slowest_hours.services` yang dihitung dari total DNS+TCP. Gunakan puncak untuk metrik terpilih atau beri label eksplisit bahwa itu puncak total akses.

10. **Sedang — biaya pembaruan detail saat Live berpotensi membesar.** SSE mengirim event untuk memicu pengambilan ulang ringkasan tanggal hari ini. Overview menghitung global dan per-device terpisah. `/api/environment` menghitung riwayat semua sensor setiap request. Cache dokumen file membantu I/O, tetapi agregasi tetap perlu diukur. Perlu pengujian 100 perangkat dan beberapa browser; belum ada angka performa yang membuktikan overload.

11. **Sedang — ringkasan suhu belum memperhitungkan cakupan pengukuran.** Suhu rata-rata global adalah rata-rata dari rata-rata per perangkat, bukan rata-rata seluruh sampel; ini sah bila ingin memberi bobot sama ke tiap perangkat, tetapi belum dijelaskan. Nilai tiap jam hanya rata-rata sehingga lonjakan singkat dapat tersembunyi. Tampilkan min/maks, jumlah sampel, waktu pengukuran terakhir, dan cakupan waktu agar 1 sampel tidak terlihat setara dengan 24 jam lengkap.

12. **Sedang — akses mobile dan banyak perangkat belum nyaman.** Tabel jaringan/suhu memiliki lebar minimum besar dan horizontal scroll di layar kecil. Belum ada pencarian ID/ruangan, filter gedung/lantai, sorting, atau pagination. Tidak perlu mengganti style yang sudah disukai: tambahkan pencarian/filter dan kolom penting dahulu; validasi visual pada perangkat nyata.

13. **Sedang — frontend perlu penanganan respons dan akses API yang seragam.** Error HTTP pada fetch ringkasan/suhu umumnya berubah menjadi pesan generik. Penyebab konfigurasi invalid, autentikasi, 404 dan timeout sulit dibedakan. Dukungan token kontrol ada melalui prompt, sedangkan akses baca utama tidak memakai alur token yang sama. Uji mode token aktif, proxy SSE, browser tanpa AbortSignal.timeout, koneksi kembali, dan konfigurasi invalid saat dashboard terbuka.

14. **Rendah–sedang — kualitas kode dan pengujian frontend.** Banyak fungsi panjang satu baris, state jaringan dan tanggal berkelindan, status/ambang berulang di frontend/backend. Risiko regresi tinggi ketika HTML atau bentuk API berubah. Tes backend tidak membuktikan layout dan interaksi browser. Perlu modul state/render/API, contract test, serta browser test untuk filter, Live, tanggal, tooltip, halaman kosong/error, dan navigasi suhu.

## Prioritas yang disarankan

1. Betulkan metrik puncak gateway/DNS/TCP dan bedakan error dari data kosong.
2. Jelaskan kondisi terakhir versus agregat 24 jam serta usia snapshot.
3. Samakan kemampuan halaman suhu: tanggal, selected-device API, lalu Live jika diperlukan.
4. Tambahkan batas ruangan, insiden dan cakupan sampel; jangan membuat status normal tanpa dasar.
5. Pencarian/filter perangkat, perbaikan mobile terarah, lalu ukur performa 100 perangkat.

Audit ini tidak mengusulkan mengganti gaya visual yang sudah disetujui pengguna. Prioritasnya ketepatan informasi dan keandalan interaksi.
