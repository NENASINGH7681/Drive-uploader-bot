# Python 3.10 Slim version (Lightweight & Fast)
FROM python:3.10-slim

# System dependencies install karein
# Note: Removed 'musl-dev' and added 'python3-dev' for better compilation
RUN apt-get update && apt-get install -y \
    gcc \
    libffi-dev \
    python3-dev \
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
