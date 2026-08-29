FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      rtl-sdr multimon-ng \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "paho-mqtt>=2.0"

WORKDIR /app
COPY p2000.py .

HEALTHCHECK --interval=5m --timeout=10s --start-period=3m \
  CMD test $(( $(date +%s) - $(cat /tmp/last_message 2>/dev/null || echo 0) )) -lt 900

CMD ["python", "-u", "p2000.py"]
