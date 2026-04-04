FROM python:3.12-slim

# Pillow needs these for font rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfreetype6 \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY templates/ ./templates/

EXPOSE 8080
EXPOSE 5000

CMD ["python", "server.py"]
