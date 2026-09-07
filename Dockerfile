FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System dependencies for psycopg2-binary and healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY alembic.ini ./
COPY migrations ./migrations
COPY core ./core
COPY db ./db
COPY models ./models
COPY api ./api
COPY bot ./bot
COPY scripts ./scripts

# non-root user
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

# Default entrypoint: bot (override with `command:` in compose for api/migrate)
CMD ["python", "-m", "bot"]
