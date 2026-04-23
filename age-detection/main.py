import os
import json
import logging
import time
from datetime import datetime, timezone

import cv2
import numpy as np
from kafka import KafkaConsumer, KafkaProducer
from minio import Minio

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"
import tensorflow as tf

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger('age-detection')

KAFKA_BOOTSTRAP      = os.environ.get('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092')
CONSUME_TOPIC        = os.environ.get('KAFKA_CONSUME_TOPIC',     'cmd.age_detection')
PRODUCE_TOPIC        = os.environ.get('KAFKA_PRODUCE_TOPIC',     'evt.age_detection.completed')
DLQ_TOPIC            = os.environ.get('KAFKA_DLQ_TOPIC',         'dead.letter.queue')
GROUP_ID             = os.environ.get('KAFKA_GROUP_ID',          'age-detection-group')
MINIO_ENDPOINT       = os.environ.get('MINIO_ENDPOINT',          'minio:9000')
MINIO_ACCESS         = os.environ.get('MINIO_ACCESS_KEY',        'minioadmin')
MINIO_SECRET         = os.environ.get('MINIO_SECRET_KEY',        'minioadmin')
MINIO_SECURE         = os.environ.get('MINIO_SECURE',            'false').lower() == 'true'
MINOR_PROB_THRESHOLD = float(os.environ.get('MINOR_PROB_THRESHOLD', '0.5'))
MODEL_PATH           = os.environ.get('MODEL_PATH', '/app/model/age_classifier.keras')
IMG_SIZE             = (200, 200)


def load_model():
    for attempt in range(10):
        if os.path.exists(MODEL_PATH):
            model = tf.keras.models.load_model(MODEL_PATH)
            logger.info('Modelo cargado desde %s', MODEL_PATH)
            return model
        logger.warning('Modelo no encontrado en %s (intento %d/10)', MODEL_PATH, attempt + 1)
        time.sleep(5)
    raise RuntimeError(f'No se encontró el modelo en {MODEL_PATH}. Ejecuta training/train_age_model.py primero.')


def build_producer():
    for i in range(15):
        try:
            return KafkaProducer(
                bootstrap_servers=KAFKA_BOOTSTRAP,
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                key_serializer=lambda k: k.encode('utf-8') if k else None,
            )
        except Exception as e:
            logger.warning('Kafka no disponible (intento %d): %s', i + 1, e)
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
            logger.warning('Kafka consumer no disponible (intento %d): %s', i + 1, e)
            time.sleep(5)
    raise RuntimeError('No se pudo conectar al consumer Kafka')


def preprocess(face_img_bgr: np.ndarray) -> np.ndarray:
    img = cv2.cvtColor(face_img_bgr, cv2.COLOR_BGR2RGB)
    img = cv2.resize(img, IMG_SIZE)
    img = img.astype(np.float32)
    img = tf.keras.applications.mobilenet_v2.preprocess_input(img)  # [-1, 1] como espera MobileNetV2
    return np.expand_dims(img, axis=0)


def classify_age(model, face_img_bgr: np.ndarray):
    """
    Devuelve (edad_estimada, es_menor, confianza).
    - edad_estimada: 12 si menor, 35 si adulto (valor representativo)
    - confianza: qué tan lejos está la probabilidad del umbral (0=inseguro, 1=máxima)
    """
    tensor    = preprocess(face_img_bgr)
    prob      = float(model.predict(tensor, verbose=0)[0][0])
    es_menor  = prob >= MINOR_PROB_THRESHOLD
    edad_estimada = 12 if es_menor else 35
    confianza     = abs(prob - 0.5) * 2
    return edad_estimada, es_menor, round(confianza, 4)


def process(msg, model, producer, minio_client):
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

    edad_estimada, es_menor, confianza = classify_age(model, face_img)

    logger.info('[%s] Cara %d: edad=%d → %s (confianza=%.3f)',
                guid, num_cara, edad_estimada, 'MENOR' if es_menor else 'ADULTO', confianza)

    producer.send(PRODUCE_TOPIC, key=guid, value={
        'guid_solicitud':   guid,
        'id_solicitud':     id_solicitud,
        'num_cara':         num_cara,
        'id_imagen':        id_imagen,
        'edad_estimada':    edad_estimada,
        'es_menor':         es_menor,
        'confianza_modelo': confianza,
        'num_total_caras':  num_total_caras,
        'minio_bucket':     raw_bucket,
        'minio_path':       raw_path,
        'x': x, 'y': y, 'w': w, 'h': h,
        'timestamp':        datetime.now(timezone.utc).isoformat(),
    })
    producer.flush()


def main():
    logger.info('Iniciando servicio de detección de edad (MobileNetV2 custom)...')
    model        = load_model()
    producer     = build_producer()
    consumer     = build_consumer()
    minio_client = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS, secret_key=MINIO_SECRET, secure=MINIO_SECURE)
    logger.info('Escuchando topic: %s', CONSUME_TOPIC)

    for message in consumer:
        msg  = message.value
        guid = msg.get('guid_solicitud', '?')
        try:
            process(msg, model, producer, minio_client)
        except Exception as e:
            logger.error('[%s] Error: %s', guid, e, exc_info=True)
            try:
                producer.send(DLQ_TOPIC, key='age-detection-error', value={
                    'service': 'age-detection', 'error': str(e), 'message': msg,
                })
                producer.flush()
            except Exception:
                pass


if __name__ == '__main__':
    main()
