FROM python:3.11-slim

# Install system deps required by Playwright/Chromium
RUN apt-get update && apt-get install -y \
  curl wget gnupg ca-certificates \
  libglib2.0-0 libnss3 libatk1.0-0 libatk-bridge2.0-0 libdrm2 \
  libxkbcommon0 libgbm1 libasound2 libxshmfence1 libxcomposite1 \
  libxdamage1 libxfixes3 libxrandr2 libxrender1 libxtst6 libxi6 \
  fonts-liberation libpango-1.0-0 libcairo2 libatspi2.0-0 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
  && python -m playwright install chromium --with-deps

# App
COPY . .

ENV PORT=8091
EXPOSE 8091

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8091"]


