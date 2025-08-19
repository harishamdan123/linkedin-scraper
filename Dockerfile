# Playwright + Chromium preinstalled (stable on Render)
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

# Only FastAPI + Uvicorn here (Playwright is in the base image)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

ENV PYTHONUNBUFFERED=1
# Render sets $PORT; default to 8080 if not provided
CMD ["sh","-c","uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
