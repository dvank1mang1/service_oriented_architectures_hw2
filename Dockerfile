FROM python:3.13-slim AS codegen
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends default-jre-headless && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir openapi-generator-cli==7.16.0
COPY openapi/ openapi/
COPY codegen/ codegen/
COPY scripts/generate.sh scripts/generate.sh
RUN sh scripts/generate.sh

FROM python:3.13-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/generated/src:/app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 app
COPY --from=codegen /build/generated/src/ generated/src/
COPY app/ app/
COPY openapi/ openapi/
COPY scripts/demo.py scripts/demo.py
USER app
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2); assert r.status == 200"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]

FROM app AS test
USER root
COPY requirements-test.txt .
RUN pip install --no-cache-dir -r requirements-test.txt
COPY tests/ tests/
COPY pyproject.toml .
USER app
CMD ["pytest", "-v", "-p", "no:cacheprovider"]
