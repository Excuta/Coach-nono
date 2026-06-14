FROM python:3.12-slim

WORKDIR /app

# System deps: libpq for psycopg2, gcc for C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY . .

# Entrypoint is set per-service in docker-compose.yml:
#   python -m coach.ingest
#   python -m coach.process
#   python -m coach.coach_service
#   streamlit run coach/dashboard.py ...
