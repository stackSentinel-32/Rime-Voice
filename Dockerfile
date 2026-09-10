FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
COPY agent/ ./agent/
COPY redis_state/ ./redis_state/
COPY web/ ./web/

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Render runs the image directly (no compose), so the boot lives here:
# migrate -> seed synthetic data -> serve on Render's injected $PORT.
# Compose overrides this CMD locally with the same command pinned to 8000.
# --ws-per-message-deflate false: per-message compression clumps streaming PCM
# into >1s holes end-to-end (measured in evidence/probe_ws_delivery.py) — audio
# must go out uncompressed, one small frame at a time.
CMD ["sh", "-c", "python -c 'from backend.db import ensure_schema, make_engine; ensure_schema(make_engine())' && python -m backend.seed && uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --ws-per-message-deflate false"]
