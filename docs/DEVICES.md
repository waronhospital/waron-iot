# Aktivasi perangkat

Daftar perangkat, gedung, lantai, dan ruangan mengikuti `config/devices.json`.

```json
{
  "id": "WMS-001",
  "name": "Sensor NICU",
  "building": "WARONHOSPITAL",
  "floor": 1,
  "room": "NICU",
  "device_key": "GANTI_KUNCI_PERANGKAT",
  "enabled": true,
  "features": {"network": true, "temperature": true}
}
```

- `enabled`: tampilkan atau sembunyikan perangkat pada dashboard.
- `features.network`: aktif/nonaktif pada dashboard jaringan.
- `features.temperature`: aktif/nonaktif pada dashboard suhu, termasuk sebelum laporan pertama.
- Kedua fitur dapat diaktifkan pada ID yang sama. Tidak perlu menambah perangkat sensor baru.

Perubahan konfigurasi dibaca otomatis saat permintaan API berikutnya. Refresh dashboard untuk melihat hasilnya. Pengaturan ini menentukan pemantauan di backend; firmware 1.20.0 selalu mengukur kedua fitur tanpa pilihan aktivasi di ESP. Perangkat tersembunyi tetap dapat mengirim laporan; riwayatnya tidak dihapus.

Konfigurasi lama tanpa `features` tetap menggunakan jenis perangkat dan fitur yang dilaporkan firmware.

## Firmware 1.20.0

ESP selalu mengukur dan mengirim jaringan serta suhu/kelembapan DHT21 pada D1 (GPIO5). Pilihan fitur di `/setup` dihapus, termasuk pengaruh pilihan lama di EEPROM. `enabled` dan `features` di `config/devices.json` hanya mengatur pemantauan dashboard. Jika sensor tidak terpasang atau gagal dibaca, `environment.sensor_ok` bernilai `false` dan nilai suhu/kelembapan `null`.
