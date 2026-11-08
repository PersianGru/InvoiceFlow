# InvoiceFlow — one image for the whole product: API, worker and web dashboards.
# The compose file runs it three times with different commands:
#   api:    uvicorn app.main:app_factory --factory          (default command)
#   worker: arq app.workers.main.WorkerSettings
#   web:    node /app/web/server.js                         (Next.js standalone; proxies /api to the api service)
#
# Build: docker build -t invoiceflow:<version> .   (Portainer: Images → Build → upload the source tarball)

# ---- 1. web dashboards (Next.js standalone build) -------------------------------------------------
FROM node:24-bookworm-slim AS webbuild
WORKDIR /src
COPY web/package.json web/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY web .
# The browser calls the API on the same origin: the web server proxies /api to the api service.
ARG NEXT_PUBLIC_API_URL=
ARG API_INTERNAL_URL=http://api:8000
# Sub-path hosting (e.g. /dashboard behind a reverse proxy that also serves a website at /): empty = root.
ARG NEXT_BASE_PATH=
ENV NEXT_TELEMETRY_DISABLED=1 NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL API_INTERNAL_URL=$API_INTERNAL_URL \
    NEXT_BASE_PATH=$NEXT_BASE_PATH
RUN npm run build

# ---- 2. runtime: Python API/worker + Node for the web server ---------------------------------------
FROM python:3.12-slim
ARG NEXT_BASE_PATH=
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production MODELS_DIR=/data/models NEXT_BASE_PATH=$NEXT_BASE_PATH
RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-deu tesseract-ocr-fra tesseract-ocr-nld tesseract-ocr-spa tesseract-ocr-ita tesseract-ocr-pol \
      libglib2.0-0 libgomp1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app && useradd -r -g app -d /app app
# the Node runtime (glibc build, same Debian base) for the web server; no npm in the runtime image
COPY --from=node:24-bookworm-slim /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY backend/pyproject.toml /app/backend/pyproject.toml
RUN pip install --no-deps /app/backend 2>/dev/null || true
COPY backend /app/backend
RUN pip install /app/backend \
    # built-in CPU engines: rapidocr (ONNX) and llama.cpp with the prebuilt CPU wheel
    && pip install --no-cache-dir "rapidocr>=3.0" "onnxruntime>=1.17" \
    && pip install --no-cache-dir "llama-cpp-python>=0.3" --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu --prefer-binary \
    && mkdir -p /data/storage /data/models && chown -R app:app /data
COPY --from=webbuild /src/.next/standalone /app/web
COPY --from=webbuild /src/.next/static /app/web/.next/static
COPY --from=webbuild /src/public /app/web/public
RUN chown -R app:app /app/web
WORKDIR /app/backend
USER app
EXPOSE 8000 3000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)" || exit 1
CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
