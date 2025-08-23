# Use Playwright's official image (includes Chromium + deps)
FROM mcr.microsoft.com/playwright/python:v1.47.0-focal

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render expects the container to listen on port 10000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
