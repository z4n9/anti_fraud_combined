# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim AS analyst-build
WORKDIR /build
COPY frontend/analyst/package.json frontend/analyst/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/analyst/ ./
RUN npm run build

FROM python:3.13-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    AMAN_DATABASE_URL=sqlite:////data/aman_bank.db \
    AMAN_ANALYST_SESSION_DIR=/data/analyst/sessions \
    AMAN_ANALYST_REGISTRY_DIR=/data/analyst/model-registry \
    AMAN_ANALYST_DICTIONARY_PATH=/data/analyst/semantic_dictionary.json \
    TMPDIR=/data/upload-tmp
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir --requirement requirements.lock \
    && groupadd --gid 10001 aman \
    && useradd --uid 10001 --gid aman --no-create-home aman \
    && mkdir -p /data/analyst \
    && chown -R aman:aman /data /app
COPY --chown=aman:aman backend/app/ backend/app/
COPY --chown=aman:aman frontend/aman/ frontend/aman/
COPY --from=analyst-build --chown=aman:aman /build/dist/ frontend/analyst/dist/
COPY --chown=aman:aman deploy/run.py deploy/healthcheck.py deploy/
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD ["python", "/app/deploy/healthcheck.py"]
CMD ["python", "/app/deploy/run.py"]
