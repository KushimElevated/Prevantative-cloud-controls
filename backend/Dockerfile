FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Hash-pinned lockfiles exported from uv.lock (see Makefile `lock`).
COPY requirements.lock requirements-dev.lock ./
# Optional extra CA (build secret "extra_ca") for TLS-intercepting proxies; empty and unused by default.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then \
      cat "$(python -c 'import pip._vendor.certifi as c; print(c.where())')" /run/secrets/extra_ca > /tmp/ca.pem; \
      export PIP_CERT=/tmp/ca.pem; \
    fi; \
    pip install --require-hashes -r requirements.lock -r requirements-dev.lock && rm -f /tmp/ca.pem

COPY . .
RUN useradd --create-home --uid 10001 ccp \
    && mkdir -p /data/exports \
    && chown -R ccp /app /data
USER ccp

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=12 \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:8000/api/health', timeout=2); sys.exit(0)"

CMD ["uvicorn", "app.asgi:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
