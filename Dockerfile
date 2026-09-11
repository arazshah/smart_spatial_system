# Smart Spatial System backend image.
#
# Build:
#   docker build -t smart-spatial-system-api .
#
# Run (see docs/DEPLOYMENT.md for the full env var reference):
#   docker run -p 8000:8000 --env-file .env -v smart-spatial-var:/app/var \
#     smart-spatial-system-api

FROM python:3.11-slim AS base

# Matches the system packages validated in .github/workflows/ci.yml —
# rasterio/geopandas/pyogrio ship GDAL in their wheels for this platform,
# these cover weasyprint's PDF rendering (pdf_renderer plugin) and a few
# transitive geospatial wheel requirements.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgdal-dev \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libharfbuzz-subset0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first so this layer is cached across code-only changes.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY . .

# Runtime state (var/) is written by a non-root user; SMART_SPATIAL_RUNTIME_DIR
# can override this to a mounted volume - see docs/DEPLOYMENT.md.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/var \
    && chown -R appuser:appuser /app
USER appuser

ENV SMART_SPATIAL_RUNTIME_DIR=/app/var \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
