#!/bin/sh
set -eu
if [ ! -f /app/config/devices.json ]; then
  echo 'ERROR: file /app/config/devices.json tidak ditemukan. Isi volume konfigurasi dengan devices.json yang benar. Lihat docs/DOCKER.md.' >&2
  exit 1
fi
if [ ! -r /app/config/devices.json ]; then
  echo 'ERROR: file konfigurasi ada tetapi tidak dapat dibaca UID 10001. Berikan izin baca file dan izin traverse direktori config pada host.' >&2
  exit 1
fi
if [ ! -w /app/data ]; then
  echo 'ERROR: /app/data tidak dapat ditulis oleh UID 10001. Periksa izin volume data.' >&2
  exit 1
fi
exec "$@"
