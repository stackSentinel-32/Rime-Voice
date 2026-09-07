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
