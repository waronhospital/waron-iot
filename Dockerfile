FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Jakarta

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 waron \
    && useradd --uid 10001 --gid waron --no-create-home waron

WORKDIR /app
COPY server.py json_storage.py ./
COPY docker/entrypoint.sh /usr/local/bin/waron-entrypoint
COPY index.html device.html temperature.html temperature-device.html logo.png ./
COPY assets/ ./assets/
COPY docs/ ./docs/
COPY config/devices.example.json ./config/devices.example.json
RUN chmod 755 /usr/local/bin/waron-entrypoint \
    && mkdir -p /app/data \
    && chown waron:waron /app/data

USER waron
EXPOSE 8000
VOLUME ["/app/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/probe', timeout=3).read()"
ENTRYPOINT ["/usr/local/bin/waron-entrypoint"]
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8000", "--config", "/app/config/devices.json"]
