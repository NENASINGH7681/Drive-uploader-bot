# Python 3.10 Slim (Stable & Light)
FROM python:3.10-slim

# System dependencies install (Fixed for Debian)
RUN apt-get update && apt-get install -y \
    gcc \
    libffi-dev \
    python3-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Working Directory
WORKDIR /app

# Install Requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Code
COPY . .

# Run Bot
CMD ["python", "bot.py"]
