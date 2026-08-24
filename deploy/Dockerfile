FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PADDLE_PDX_MODEL_SOURCE=BOS \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
    PADDLE_PDX_CACHE_HOME=/srv/models/paddlex \
    PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=False \
    YXRT_MODE=cloud \
    YXRT_HOST=0.0.0.0 \
    YXRT_PORT=8765 \
    YXRT_DATA_DIR=/srv/data \
    YXRT_UPLOAD_DIR=/srv/uploads \
    YXRT_MODEL_DIR=/srv/models

WORKDIR /srv/app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libgl1 libglib2.0-0 curl && rm -rf /var/lib/apt/lists/*
COPY requirements-core.txt requirements-ocr.txt ./
RUN pip install --no-cache-dir -r requirements-core.txt -r requirements-ocr.txt
COPY app ./app
COPY scripts ./scripts
RUN mkdir -p /srv/data /srv/uploads /srv/models /srv/tmp && python scripts/prefetch_ocr_models.py
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://127.0.0.1:8765/health || exit 1
CMD ["python", "scripts/start_server.py"]
