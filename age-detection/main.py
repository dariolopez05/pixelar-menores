import os
import json
import logging
import time
from datetime import datetime, timezone

os.environ.setdefault('DEEPFACE_HOME', '/app/model')

import cv2
import numpy as np
from deepface import DeepFace
from kafka import KafkaConsumer, KafkaProducer
from minio import Minio

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger('age-detection')

KAFKA_BOOTSTRAP = os.environ.get('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092')
CONSUME_TOPIC   = os.environ.get('KAFKA_CONSUME_TOPIC',     'cmd.age_detection')
PRODUCE_TOPIC   = os.environ.get('KAFKA_PRODUCE_TOPIC',     'evt.age_detection.completed')
DLQ_TOPIC       = os.environ.get('KAFKA_DLQ_TOPIC',         'dead.letter.queue')
GROUP_ID        = os.environ.get('KAFKA_GROUP_ID',          'age-detection-group')
MINIO_ENDPOINT  = os.environ.get('MINIO_ENDPOINT',          'minio:9000')
MINIO_ACCESS    = os.environ.get('MINIO_ACCESS_KEY',        'minioadmin')
MINIO_SECRET    = os.environ.get('MINIO_SECRET_KEY',        'minioadmin')
MINIO_SECURE    = os.environ.get('MINIO_SECURE',            'false').lower() == 'true'
MINOR_THRESHOLD = int(os.environ.get('MINOR_AGE_THRESHOLD', '18'))


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


def estimate_age(face_img):
    try:
        result = DeepFace.analyze(face_img, actions=['age'], enforce_detection=False, silent=True)
        return float(result[0]['age']), 0.85
    except Exception as e:
        logger.warning(f'DeepFace falló: {e} — usando edad por defecto (25)')
        return 25.0, 0.0


def process(msg, producer, minio_client):
    guid            = msg['guid_solicitud']
    id_solicitud    = msg['id_solicitud']
    num_cara        = msg['num_cara']
    id_imagen       = msg['id_imagen']
    crops_bucket    = msg.get('face_crops_bucket', 'face-crops')
    crops_path      = msg['face_crops_path']
    raw_bucket      = msg.get('minio_bucket', 'raw-images')
    raw_path        = msg['minio_path']
    num_total_caras = msg['num_total_caras']
    x, y, w, h      = msg['x'], msg['y'], msg['w'], msg['h']

    response  = minio_client.get_object(crops_bucket, crops_path)
    img_bytes = response.read()
    response.close()
    response.release_conn()

    nparr    = np.frombuffer(img_bytes, np.uint8)
    face_img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    age, confidence = estimate_age(face_img)
    es_menor        = age < MINOR_THRESHOLD

    logger.info(f'[{guid}] Cara {num_cara}: {age:.1f} años → {"MENOR" if es_menor else "ADULTO"}')

    producer.send(PRODUCE_TOPIC, key=guid, value={
        'guid_solicitud':    guid,
        'id_solicitud':      id_solicitud,
        'num_cara':          num_cara,
        'id_imagen':         id_imagen,
        'edad_estimada':     round(age, 1),
        'es_menor':          es_menor,
        'confianza_modelo':  round(confidence, 4),
        'num_total_caras':   num_total_caras,
        'minio_bucket':      raw_bucket,
        'minio_path':        raw_path,
        'x': x, 'y': y, 'w': w, 'h': h,
        'timestamp':         datetime.now(timezone.utc).isoformat(),
    })
    producer.flush()


def main():
    logger.info('Iniciando servicio de detección de edad...')
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
                producer.send(DLQ_TOPIC, key='age-detection-error', value={
                    'service': 'age-detection', 'error': str(e), 'message': msg,
                })
                producer.flush()
            except Exception:
                pass


if __name__ == '__main__':
    main()
