# Hugging Face Spaces (Docker SDK) — runs the app on HF's port 7860.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    AEGIS_DEVICE=cpu \
    AEGIS_ENV=production

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Track record needs a writable dir on Spaces.
RUN mkdir -p data && chmod -R 777 data

EXPOSE 7860
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "7860"]
