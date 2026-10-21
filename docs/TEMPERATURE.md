# WARON IoT — D1 Mini dengan dua fitur

Firmware utama: `d1mini-netpulse/d1mini-netpulse.ino` versi 1.9.0.
Gunakan firmware ini untuk jaringan saja, suhu saja, atau keduanya sekaligus.
Folder esp32-temperature adalah pengembangan terpisah sebelumnya, bukan firmware yang dipakai untuk kebutuhan ini. Sketch asli tetap disimpan.

## Sensor dan pin

Sensor DHT21 mengikuti tipe pada sketch asli. DATA disambungkan ke **D1 pada board D1 Mini = GPIO5**, bukan GPIO1 atau GPIO13. GND ke GND. Gunakan catu 3.3V dan pull-up DATA ke 3.3V sesuai modul sensor. Instal library DHT sensor library by Adafruit dan Adafruit Unified Sensor.

## Pengaturan

Upload firmware D1 Mini melalui USB atau OTA yang sudah tersedia. Buka `/setup` dengan login yang sama. Pada Backend & telemetri tersedia:

- Pemantauan jaringan: pengujian lokal dan empat layanan.
- Sensor suhu & kelembapan: DHT21 pada D1, dibaca tiap 3 detik.

Centang salah satu atau keduanya, lalu Simpan Pengaturan. Minimal satu fitur harus aktif. Pengaturan tersimpan setelah restart; upgrade dari firmware lama memakai default jaringan aktif, suhu nonaktif. Interval telemetri tetap diatur dalam detik/menit di form yang sama; Live tetap memakai mekanisme dashboard yang ada.

Gunakan satu ID dan kunci perangkat yang sama, misalnya WMS-001, dengan kind network pada konfigurasi backend. Tidak perlu mendaftarkan sensor sebagai perangkat kedua. Backend menerima fitur dan environment bersama data jaringan. Jika hanya suhu aktif, perangkat tidak masuk ringkasan jaringan. Jika keduanya aktif, perangkat muncul pada pemantauan jaringan serta `/temperature`. Sensor nonaktif tidak ditampilkan pada halaman suhu setelah laporan berikutnya diterima.

Halaman lokal `/` menampilkan fitur aktif, suhu, kelembapan dan status pengiriman. Sensor gagal mengirim nilai null, bukan angka nol atau nilai lama. Backend `/api/environment` menyediakan hasil suhu dan riwayat 24 jam; `/api/v1/devices` menggabungkan data kedua fitur dalam satu perangkat. Restart backend setelah perubahan kode.

Contoh bagian payload:

```json
{
  "features": {"network": true, "temperature": true},
  "environment": {
    "sensor_ok": true,
    "temperature_c": 24.5,
    "humidity_percent": 60.0,
    "reading_age_ms": 500
  }
}
```

Belum ada pengujian sensor fisik atau alarm ambang suhu otomatis.
