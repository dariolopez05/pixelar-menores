import os
import json
import logging
import time
from datetime import datetime, timezone
from io import BytesIO

import cv2
import numpy as np
from kafka import KafkaConsumer, KafkaProducer
from minio import Minio

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger('pixelation')

KAFKA_BOOTSTRAP   = os.environ.get('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092')
CONSUME_TOPIC     = os.environ.get('KAFKA_CONSUME_TOPIC',     'cmd.pixelation')
PRODUCE_TOPIC     = os.environ.get('KAFKA_PRODUCE_TOPIC',     'evt.pixelation.completed')
DLQ_TOPIC         = os.environ.get('KAFKA_DLQ_TOPIC',         'dead.letter.queue')
GROUP_ID          = os.environ.get('KAFKA_GROUP_ID',          'pixelation-group')
MINIO_ENDPOINT    = os.environ.get('MINIO_ENDPOINT',          'minio:9000')
MINIO_ACCESS      = os.environ.get('MINIO_ACCESS_KEY',        'minioadmin')
MINIO_SECRET      = os.environ.get('MINIO_SECRET_KEY',        'minioadmin')
MINIO_SECURE      = os.environ.get('MINIO_SECURE',            'false').lower() == 'true'
PIXEL_BLOCK_SIZE  = int(os.environ.get('PIXEL_BLOCK_SIZE',    '20'))


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


def pixelar(img, x, y, w, h, block):
    h_img, w_img = img.shape[:2]
    x2, y2 = min(x + w, w_img), min(y + h, h_img)
    roi     = img[y:y2, x:x2]
    small   = cv2.resize(roi, (max(1, (x2-x)//block), max(1, (y2-y)//block)), interpolation=cv2.INTER_LINEAR)
    img[y:y2, x:x2] = cv2.resize(small, (x2-x, y2-y), interpolation=cv2.INTER_NEAREST)
    return img


def process(msg, producer, minio_client):
    guid          = msg['guid_solicitud']
    id_solicitud  = msg['id_solicitud']
    bucket_src    = msg.get('minio_bucket_origen',  'raw-images')
    path_src      = msg['minio_path_origen']
    bucket_dst    = msg.get('minio_bucket_destino', 'processed-images')
    path_dst      = msg['minio_path_destino']
    caras_menores = msg.get('caras_menores', [])

    t0 = time.time()

    response  = minio_client.get_object(bucket_src, path_src)
    img_bytes = response.read()
    response.close()
    response.release_conn()

    nparr = np.frombuffer(img_bytes, np.uint8)
    img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    for cara in caras_menores:
        img = pixelar(img, cara['x'], cara['y'], cara['w'], cara['h'], PIXEL_BLOCK_SIZE)
        logger.info(f'[{guid}] Cara {cara.get("num_cara", "?")} pixelada')

    ext       = '.' + path_src.rsplit('.', 1)[-1] if '.' in path_src else '.jpg'
    _, buffer = cv2.imencode(ext, img)
    out_bytes = buffer.tobytes()

    minio_client.put_object(bucket_dst, path_dst, BytesIO(out_bytes), len(out_bytes), content_type='image/jpeg')
    duracion_ms = int((time.time() - t0) * 1000)
    logger.info(f'[{guid}] Imagen pixelada subida → {bucket_dst}/{path_dst} ({duracion_ms} ms)')

    producer.send(PRODUCE_TOPIC, key=guid, value={
        'guid_solicitud':  guid,
        'id_solicitud':    id_solicitud,
        'minio_bucket':    bucket_dst,
        'minio_path':      path_dst,
        'caras_pixeladas': len(caras_menores),
        'duracion_ms':     duracion_ms,
        'timestamp':       datetime.now(timezone.utc).isoformat(),
    })
    producer.flush()


def main():
    logger.info('Iniciando Pixelation Service...')
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
                producer.send(DLQ_TOPIC, key='pixelation-error', value={
                    'service': 'pixelation', 'error': str(e), 'message': msg
                })
                producer.flush()
            except Exception:
                pass


if __name__ == '__main__':
    main()
