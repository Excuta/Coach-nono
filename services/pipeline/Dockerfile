FROM python:3.12-slim

WORKDIR /app

# System deps: libpq for psycopg2, gcc for C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl \
 && rm -rf /var/lib/apt/lists/*

# Install deps first so this layer is cached unless pyproject.toml changes.
COPY pyproject.toml .
RUN python3 -c "import tomllib; deps=tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']; print('\n'.join(deps))" \
    | xargs pip install --no-cache-dir

# Copy source and install the package itself (deps already present).
COPY . .
RUN pip install --no-cache-dir --no-deps .

# Entrypoint is set per-service in docker-compose.yml:
#   python -m coach.ingest
#   python -m coach.process
#   python -m coach.coach_service
#   streamlit run coach/dashboard.py ...
