FROM python:3.12-slim

LABEL org.opencontainers.image.title="BinderBridge"
LABEL org.opencontainers.image.description="Self-hosted trading card collection and trade manager"
LABEL org.opencontainers.image.licenses="AGPL-3.0"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV BINDERBRIDGE_HOST=0.0.0.0
ENV BINDERBRIDGE_PORT=8000
ENV BINDERBRIDGE_DATA=/data

WORKDIR /app

RUN useradd --create-home --home-dir /home/binderbridge --shell /usr/sbin/nologin binderbridge \
    && mkdir -p /data /config \
    && chown -R binderbridge:binderbridge /data /config

COPY --chown=binderbridge:binderbridge app.py /app/app.py
COPY --chown=binderbridge:binderbridge binderbridge /app/binderbridge
COPY --chown=binderbridge:binderbridge scripts /app/scripts
COPY --chown=binderbridge:binderbridge static /app/static
COPY --chown=binderbridge:binderbridge binderbridge.example.ini /app/binderbridge.example.ini

USER binderbridge

EXPOSE 8000

VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import json, os, urllib.request; port=os.environ.get('BINDERBRIDGE_PORT', os.environ.get('PORT', '8000')); data=json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/health', timeout=3)); raise SystemExit(0 if data.get('ok') else 1)"

CMD ["python", "app.py"]
