FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Berlin

WORKDIR /app

# Playwright-Abhängigkeiten nur installieren, wenn ein Portal sie braucht.
# Das Basisimage bleibt sonst rund 400 MB kleiner.
ARG MIT_PLAYWRIGHT=false

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential libxml2-dev libxslt1-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

RUN if [ "$MIT_PLAYWRIGHT" = "true" ]; then playwright install --with-deps chromium; fi

COPY src/ src/
COPY config/ config/
COPY dashboard/ dashboard/
COPY pyproject.toml README.md ./
RUN pip install -e .

RUN mkdir -p data logs docs/data && \
    useradd --create-home --uid 1000 agent && chown -R agent:agent /app
USER agent

HEALTHCHECK --interval=5m --timeout=10s --start-period=30s \
  CMD python -c "from wohnungsagent.config.profil import lade_profil; lade_profil('config/suchprofil.yml')" || exit 1

# Standard ist das Dashboard mit dem Suchknopf – nicht der Scheduler.
# Einzelner Lauf: docker run ... python -m wohnungsagent.cli scan
CMD ["streamlit", "run", "dashboard/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
