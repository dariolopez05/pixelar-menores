FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN python -c "from deepface import DeepFace; DeepFace.build_model('Age')" 2>/dev/null \
    || echo '[WARN] Pesos de DeepFace se descargarán en el primer arranque'

COPY . .

CMD ["python", "main.py"]
