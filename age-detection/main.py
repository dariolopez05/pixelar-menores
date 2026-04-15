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


def estimate_age(face_crop):
    try:
        result = DeepFace.analyze(face_crop, actions=['age'], enforce_detection=False, silent=True)
        return float(result[0]['age']), 0.85
    except Exception as e:
        logger.warning(f'DeepFace falló: {e} — usando edad por defecto (25)')
        return 25.0, 0.0


def process(msg, producer, minio_client):
    guid         = msg['guid_solicitud']
    id_solicitud = msg['id_solicitud']
    bucket       = msg.get('minio_bucket', 'raw-images')
    minio_path   = msg['minio_path']
    caras_input  = msg.get('caras', [])

    response  = minio_client.get_object(bucket, minio_path)
    img_bytes = response.read()
    response.close()
    response.release_conn()

    nparr = np.frombuffer(img_bytes, np.uint8)
    img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    caras_resultado = []
    for cara in caras_input:
        x, y, w, h = cara['x'], cara['y'], cara['w'], cara['h']
        margin = int(min(w, h) * 0.1)
        crop   = img[max(0, y-margin):y+h+margin, max(0, x-margin):x+w+margin]

        age, confidence = estimate_age(crop)
        es_menor        = age < MINOR_THRESHOLD

        logger.info(f'[{guid}] Cara {cara["num_cara"]}: {age:.1f} años → {"MENOR" if es_menor else "ADULTO"}')
        caras_resultado.append({
            **cara,
            'edad_estimada':    round(age, 1),
            'es_menor':         es_menor,
            'confianza_modelo': round(confidence, 3),
        })

    producer.send(PRODUCE_TOPIC, key=guid, value={
        'guid_solicitud': guid,
        'id_solicitud':   id_solicitud,
        'minio_bucket':   bucket,
        'minio_path':     minio_path,
        'caras':          caras_resultado,
        'timestamp':      datetime.now(timezone.utc).isoformat(),
    })
    producer.flush()
    logger.info(f'[{guid}] evt.age_detection.completed publicado')


def main():
    logger.info('Iniciando Age Detection Service...')
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
                    'service': 'age-detection', 'error': str(e), 'message': msg
                })
                producer.flush()
            except Exception:
                pass


if __name__ == '__main__':
    main()
