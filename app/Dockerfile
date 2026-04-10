FROM python:3.12-slim AS base

# WeasyPrint needs Pango/Cairo for layout and font rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfreetype6 \
    fonts-dejavu-core \
    fontconfig \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libglib2.0-0t64 \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
COPY pyproject.toml uv.lock ./


FROM base AS runtime

RUN uv export --no-dev --frozen | pip install --no-cache-dir -r /dev/stdin

COPY *.py ./
COPY templates/ ./templates/
COPY static/ ./static/

EXPOSE 8080
EXPOSE 5000

CMD ["python", "server.py"]


FROM base AS test

RUN uv export --frozen | pip install --no-cache-dir -r /dev/stdin

COPY . .

CMD ["python", "-m", "pytest"]
