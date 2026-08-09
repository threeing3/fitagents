FROM node:20-alpine AS web-builder

WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8000

WORKDIR /app

COPY fast_api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && useradd --create-home --uid 10001 appuser

COPY fast_api/ ./fast_api/
COPY alembic/ ./alembic/
COPY alembic.ini ./alembic.ini
COPY --from=web-builder /build/web/dist ./web/dist/

USER appuser
EXPOSE 8000

CMD ["sh", "-c", "uvicorn fast_api.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
