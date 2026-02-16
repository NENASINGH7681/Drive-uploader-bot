# Python 3.10 Slim version (Lightweight & Fast)
FROM python:3.10-slim-buster

# System dependencies install karein (gcc tgcrypto ke liye zaroori hai)
RUN apt-get update && apt-get install -y \
    gcc \
    libffi-dev \
    musl-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Work Directory set karein
WORKDIR /app

# Requirements copy aur install karein
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Baaki code copy karein
COPY . .

# Bot start karein
CMD ["python", "bot.py"]
