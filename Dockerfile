FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

WORKDIR /app

# Install Python dependencies first (layer-cached as long as requirements.txt unchanged).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (in case the base image needs updates or specific ones)
RUN playwright install chromium

# Copy application code.
COPY app ./app

# Env defaults — can be overridden at runtime via Render/Docker env.
ENV PYTHONUNBUFFERED=1 \
    PORT=10000

EXPOSE 10000

# Render expects the process to bind to $PORT.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
