FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /training

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY train_age_model.py .
COPY train_age_model_v2.py .

ENV DATASET_DIR=/dataset
ENV OUTPUT_DIR=/model
ENV REPORTS_DIR=/reports

CMD ["python", "train_age_model_v2.py"]
