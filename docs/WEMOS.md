# Wemos D1 Mini — pembaruan data nyata

**Versi portal setup dan OTA:** gunakan [panduan d1mini-netpulse](../d1mini-netpulse/README.md). Panduan di bawah untuk contoh firmware awal dengan config.h.

Satu ESP8266 Wemos D1 Mini per lantai. Firmware: `firmware/netpulse/netpulse.ino`.

## 1. Siapkan server

Gunakan alamat LAN server yang tetap (DHCP reservation direkomendasikan). Contoh 192.168.1.10, ganti sesuai jaringan Anda.

```sh
python3 server.py --register-floor 1
python3 server.py --host 0.0.0.0 --port 8000
```

Simpan `device_id` dan `device_key` yang muncul. Ulangi pendaftaran untuk lantai lain; setiap perangkat menggunakan kunci sendiri. Buka http://192.168.1.10:8000/api/probe dari perangkat lain di Wi-Fi yang sama. Harus menghasilkan `{"status":"ok","service":"netpulse"}` (spasi JSON bisa berbeda). Izinkan TCP 8000 pada firewall. Client isolation/VLAN harus mengizinkan Wemos mengakses server.

## 2. Siapkan Arduino IDE

Tambahkan URL Boards Manager ESP8266:

```text
https://arduino.esp8266.com/stable/package_esp8266com_index.json
```

Instal paket **esp8266 by ESP8266 Community** lalu pilih board **LOLIN(WEMOS) D1 R2 & mini**. Library WiFi dan HTTPClient sudah termasuk dalam core, tanpa ArduinoJson.

Buka `firmware/netpulse/netpulse.ino`. Salin `config.example.h` menjadi `config.h` pada direktori yang sama dan isi:

- `WIFI_SSID` dan `WIFI_PASSWORD`: jaringan 2,4 GHz yang ingin diuji.
- `SERVER_URL`: `http://IP-LAN-SERVER:8000`, tanpa garis miring terakhir. Jangan gunakan localhost karena itu menunjuk Wemos sendiri.
- `DEVICE_ID`, `DEVICE_KEY`: hasil pendaftaran lantai.
- `INTERNET_PROBE_URL`: endpoint **HTTP di luar LAN**, milik Anda, yang selalu merespons **204** tanpa redirect atau login. Kosong berarti belum ada pengujian internet; dashboard menampilkan belum diketahui.

Contoh endpoint pada VPS dengan Nginx, bila Anda sudah mengelola VPS tersebut:

```nginx
location = /netpulse-probe {
    add_header Cache-Control "no-store" always;
    return 204;
}
```

Firmware awal mengukur HTTP biasa, bukan HTTPS. URL HTTPS memerlukan WiFiClientSecure dan validasi sertifikat; jangan sekadar mengganti skema atau mematikan validasi. Satu endpoint gagal berarti akses ke endpoint tersebut gagal, belum membuktikan seluruh internet mati. Pengujian beberapa tujuan/DNS terpisah belum diterapkan.

## 3. Upload dan verifikasi

Hubungkan kabel USB data, pilih port lalu Upload. Buka Serial Monitor 115200 baud. `Telemetri HTTP 201` berarti backend menerima data. Setelah dashboard refresh (maksimal sekitar 5 detik), lantai menjadi online.

- 401: ID atau kunci perangkat salah.
- 400: payload tidak sesuai kontrak.
- Nilai negatif: koneksi/request HTTP gagal.
- Tidak ada log: cek SSID/password, jaringan 2,4 GHz, dan catu daya.
- Online tetapi internet belum diketahui: probe URL belum diisi atau belum ada uji baru.

## Alur waktu

Wemos mengirim tiap sekitar 10 detik, uji internet tiap sekitar 30 detik. Pengujian bersifat sinkron; koneksi buruk/DNS dapat memperpanjang siklus. Dashboard polling API tiap 5 detik. Ini pemantauan mendekati realtime, bukan streaming instan.

`local_ms`: durasi GET lokal hingga header respons. `internet_ms`: durasi GET endpoint internet hingga header respons; termasuk proses koneksi/resolusi yang terjadi, bukan ping atau bandwidth. Hanya status 204 dianggap internet berhasil untuk mencegah halaman login dianggap sukses. Pada siklus tanpa uji internet, `internet_ok` dan `internet_ms` dikirim null supaya pengujian lama tidak dihitung ulang.

Jika Wi-Fi putus, pengiriman ikut terhenti. Server menandai offline setelah 35 detik tanpa data; firmware mencoba reconnect otomatis. Tidak ada penyimpanan/replay hasil saat offline. Wemos yang terjangkau belum tentu internetnya baik.

Backend HTTP dan kunci perangkat ini ditujukan untuk LAN tepercaya. Untuk lintas internet, gunakan reverse proxy HTTPS dan firmware dengan validasi TLS.

Referensi: [panduan Arduino Wemos](https://www.wemos.cc/en/latest/tutorials/d1/get_started_with_arduino_d1.html), [contoh resmi HTTPClient](https://github.com/esp8266/Arduino/blob/master/libraries/ESP8266HTTPClient/examples/BasicHttpClient/BasicHttpClient.ino), [HTTPClient API](https://github.com/esp8266/Arduino/blob/master/libraries/ESP8266HTTPClient/src/ESP8266HTTPClient.h).

## Membuat isian setup untuk perangkat terdaftar

```sh
python3 server.py --issue-setup-floor 1 --server-url http://IP-SERVER:8000
```

Membuat kunci tambahan dan file privat `data/setup-WMS-001.txt`. Kunci utama tetap diterima; restart backend setelah memperbarui kode server. File berisi kunci asli, jangan dibagikan atau dimasukkan Git. SSID/password jaringan tetap diisi sesuai Wi-Fi gedung. Probe internet dikosongkan sampai endpoint eksternal tersedia.
