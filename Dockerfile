FROM python:3.10-slim

# Aria2 install kar rahe hain High Speed ke liye
RUN apt-get update && apt-get install -y \
    gcc \
    libffi-dev \
    python3-dev \
    ffmpeg \
    aria2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "bot.py"]
