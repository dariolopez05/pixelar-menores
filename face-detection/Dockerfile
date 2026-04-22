FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# El modelo debe estar en face-detection/yolov8n-face.pt antes del build
COPY yolov8n-face.pt /app/yolov8n-face.pt

COPY . .

CMD ["python", "main.py"]
