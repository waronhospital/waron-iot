#!/bin/sh
set -eu
if [ ! -r /app/config/devices.json ]; then
  echo 'ERROR: /app/config/devices.json tidak tersedia. Buat config/devices.json dari config/devices.example.json dan sesuaikan perangkat.' >&2
  exit 1
fi
if [ ! -w /app/data ]; then
  echo 'ERROR: /app/data tidak dapat ditulis oleh UID 10001. Periksa izin volume data.' >&2
  exit 1
fi
exec "$@"
