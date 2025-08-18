# ✅ Official Playwright image (Chromium + fonts + deps already installed)
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

# Install only FastAPI/uvicorn (Playwright is already in the base image)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

ENV PORT=8080
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
