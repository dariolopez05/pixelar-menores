import os
import json
import logging
import time
from datetime import datetime, timezone

import cv2
import numpy as np
import tensorflow as tf
from concurrent.futures import ThreadPoolExecutor, as_completed
from tensorflow.keras.applications.efficientnet_v2 import preprocess_input
from kafka import KafkaConsumer, KafkaProducer
from minio import Minio

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
IMG_SIZE             = (224, 224)


def load_model():
    for attempt in range(10):
        if os.path.exists(MODEL_PATH):
            model = tf.keras.models.load_model(MODEL_PATH)
            logger.info('Modelo cargado desde %s', MODEL_PATH)
            return model
        logger.warning('Modelo no encontrado en %s (intento %d/10)', MODEL_PATH, attempt + 1)
        time.sleep(5)
    raise RuntimeError(f'No se encontró el modelo en {MODEL_PATH}.')


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
    # Padding cuadrado para no distorsionar la cara
    h, w = img.shape[:2]
    if h != w:
        size  = max(h, w)
        pad_t = (size - h) // 2
        pad_b = size - h - pad_t
        pad_l = (size - w) // 2
        pad_r = size - w - pad_l
        img   = cv2.copyMakeBorder(img, pad_t, pad_b, pad_l, pad_r, cv2.BORDER_REPLICATE)
    img = cv2.resize(img, IMG_SIZE)
    img = preprocess_input(img.astype(np.float32))
    return np.expand_dims(img, axis=0)


def _download_crop(minio_client, cara):
    bucket = cara.get('face_crops_bucket', 'face-crops')
    path   = cara['face_crops_path']
    resp   = minio_client.get_object(bucket, path)
    data   = resp.read()
    resp.close(); resp.release_conn()
    nparr  = np.frombuffer(data, np.uint8)
    return cv2.imdecode(nparr, cv2.IMREAD_COLOR)


def process(msg, model, producer, minio_client):
    guid            = msg['guid_solicitud']
    id_solicitud    = msg['id_solicitud']
    num_total_caras = msg['num_total_caras']
    raw_bucket      = msg.get('minio_bucket', 'raw-images')
    raw_path        = msg['minio_path']
    caras           = msg['caras']

    # Descarga paralela de todos los recortes
    imgs = {}
    with ThreadPoolExecutor(max_workers=min(len(caras), 8)) as pool:
        futures = {pool.submit(_download_crop, minio_client, c): c for c in caras}
        for fut in as_completed(futures):
            cara = futures[fut]
            imgs[cara['num_cara']] = fut.result()

    # Batch tensor (N, 224, 224, 3) — orden por num_cara para que probs[i] coincida
    caras_sorted = sorted(caras, key=lambda c: c['num_cara'])
    batch = np.concatenate([preprocess(imgs[c['num_cara']]) for c in caras_sorted], axis=0)

    # Una sola inferencia para todas las caras
    probs = model.predict(batch, verbose=0)  # shape (N, 1)

    now = datetime.now(timezone.utc).isoformat()
    for i, cara in enumerate(caras_sorted):
        prob          = float(probs[i][0])
        es_menor      = prob >= MINOR_PROB_THRESHOLD
        edad_estimada = 12 if es_menor else 35
        confianza     = round(abs(prob - 0.5) * 2, 4)
        logger.info('  → Cara %d: prob_raw=%.4f | threshold=%.2f | clasificado=%s',
                    cara['num_cara'], prob, MINOR_PROB_THRESHOLD, 'MENOR' if es_menor else 'adulto')
        producer.send(PRODUCE_TOPIC, key=guid, value={
            'guid_solicitud':   guid,
            'id_solicitud':     id_solicitud,
            'num_cara':         cara['num_cara'],
            'id_imagen':        cara['id_imagen'],
            'edad_estimada':    edad_estimada,
            'es_menor':         es_menor,
            'confianza_modelo': confianza,
            'num_total_caras':  num_total_caras,
            'minio_bucket':     raw_bucket,
            'minio_path':       raw_path,
            'x': cara['x'], 'y': cara['y'], 'w': cara['w'], 'h': cara['h'],
            'timestamp':        now,
        })

    producer.flush()
    logger.info('[%s] Batch de %d cara(s) clasificado en 1 inferencia', guid, len(caras_sorted))


def main():
    logger.info('Iniciando servicio de detección de edad (EfficientNetV2S Keras)...')
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
