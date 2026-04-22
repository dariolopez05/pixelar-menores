import os
import json
import logging
import time
from datetime import datetime, timezone

import cv2
import numpy as np
from ultralytics import YOLO
from kafka import KafkaConsumer, KafkaProducer
from minio import Minio

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger('face-detection')

KAFKA_BOOTSTRAP  = os.environ.get('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092')
CONSUME_TOPIC    = os.environ.get('KAFKA_CONSUME_TOPIC',     'cmd.face_detection')
PRODUCE_TOPIC    = os.environ.get('KAFKA_PRODUCE_TOPIC',     'evt.face_detection.completed')
DLQ_TOPIC        = os.environ.get('KAFKA_DLQ_TOPIC',         'dead.letter.queue')
GROUP_ID         = os.environ.get('KAFKA_GROUP_ID',          'face-detection-group')
MINIO_ENDPOINT   = os.environ.get('MINIO_ENDPOINT',          'minio:9000')
MINIO_ACCESS     = os.environ.get('MINIO_ACCESS_KEY',        'minioadmin')
MINIO_SECRET     = os.environ.get('MINIO_SECRET_KEY',        'minioadmin')
MINIO_SECURE     = os.environ.get('MINIO_SECURE',            'false').lower() == 'true'
MODEL_PATH     = os.environ.get('YOLO_MODEL_PATH',     '/app/yolov8n-face.pt')
CONF_THRESHOLD = float(os.environ.get('YOLO_CONF_THRESHOLD', '0.4'))

def _load_model() -> YOLO:
    if os.path.exists(MODEL_PATH):
        logger.info(f'Cargando modelo YOLO desde {MODEL_PATH}...')
        m = YOLO(MODEL_PATH)
    else:
        # Ultralytics descarga automáticamente yolov8n-face.pt si no está en local
        logger.warning(f'{MODEL_PATH} no encontrado — descargando con ultralytics...')
        m = YOLO('yolov8n-face.pt')
    logger.info('Modelo YOLO listo')
    return m


model = _load_model()


def build_producer():
    for i in range(15):
        try:
            return KafkaProducer(
                bootstrap_servers=KAFKA_BOOTSTRAP,
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                key_serializer=lambda k: k.encode('utf-8') if k else None,
            )
        except Exception as e:
            logger.warning(f'Kafka no disponible (intento {i+1}): {e}')
            time.sleep(5)
    raise RuntimeError('No se pudo conectar a Kafka')


def build_consumer():
    for i in range(15):
        try:
            return KafkaConsumer(
                CONSUME_TOPIC,
                bootstrap_servers=KAFKA_BOOTSTRAP,
                group_id=GROUP_ID,
                auto_offset_reset='earliest',
                enable_auto_commit=True,
                value_deserializer=lambda v: json.loads(v.decode('utf-8')),
            )
        except Exception as e:
            logger.warning(f'Kafka consumer no disponible (intento {i+1}): {e}')
            time.sleep(5)
    raise RuntimeError('No se pudo conectar al consumer Kafka')


def detect_faces(img_bytes: bytes) -> list[dict]:
    nparr = np.frombuffer(img_bytes, np.uint8)
    img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError('No se pudo decodificar la imagen')

    results = model(img, conf=CONF_THRESHOLD, verbose=False)
    caras   = []
    for i, box in enumerate(results[0].boxes):
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        conf = float(box.conf[0])
        caras.append({
            'num_cara':  i + 1,
            'x':         int(x1),
            'y':         int(y1),
            'w':         int(x2 - x1),
            'h':         int(y2 - y1),
            'confianza': round(conf, 3),
        })
    return caras


def process(msg, producer, minio_client):
    guid         = msg['guid_solicitud']
    id_solicitud = msg['id_solicitud']
    bucket       = msg.get('minio_bucket', 'raw-images')
    minio_path   = msg['minio_path']

    t0 = time.time()
    response  = minio_client.get_object(bucket, minio_path)
    img_bytes = response.read()
    response.close()
    response.release_conn()

    caras       = detect_faces(img_bytes)
    duracion_ms = int((time.time() - t0) * 1000)
    logger.info(f'[{guid}] {len(caras)} cara(s) detectada(s) en {duracion_ms} ms')

    producer.send(PRODUCE_TOPIC, key=guid, value={
        'guid_solicitud': guid,
        'id_solicitud':   id_solicitud,
        'minio_bucket':   bucket,
        'minio_path':     minio_path,
        'caras':          caras,
        'duracion_ms':    duracion_ms,
        'timestamp':      datetime.now(timezone.utc).isoformat(),
    })
    producer.flush()


def main():
    logger.info('Iniciando servicio de detección de caras (YOLOv8)...')
    producer     = build_producer()
    consumer     = build_consumer()
    minio_client = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS, secret_key=MINIO_SECRET, secure=MINIO_SECURE)
    logger.info(f'Escuchando topic: {CONSUME_TOPIC}')

    for message in consumer:
        msg  = message.value
        guid = msg.get('guid_solicitud', '?')
        try:
            process(msg, producer, minio_client)
        except Exception as e:
            logger.error(f'[{guid}] Error: {e}', exc_info=True)
            try:
                producer.send(DLQ_TOPIC, key='face-detection-error', value={
                    'service': 'face-detection', 'error': str(e), 'message': msg,
                })
                producer.flush()
            except Exception:
                pass


if __name__ == '__main__':
    main()
