FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY dograh ./dograh

RUN mkdir -p /app/data/leads /app/data/calls /app/data/transcripts /app/data/batches

EXPOSE 8001

# Single worker keeps in-memory sequential batch runner state consistent
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]