FROM python:3.12-slim

# Pillow needs these for font rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfreetype6 \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
COPY pyproject.toml uv.lock ./
RUN uv export --no-dev --frozen | pip install --no-cache-dir -r /dev/stdin

COPY *.py ./
COPY templates/ ./templates/
COPY static/ ./static/

EXPOSE 8080
EXPOSE 5000

CMD ["python", "server.py"]
