import os
import json
import logging
import time
from datetime import datetime, timezone, timedelta

import psycopg2
from kafka import KafkaConsumer, KafkaProducer
from minio import Minio

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger('storage-service')

KAFKA_BOOTSTRAP  = os.environ.get('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092')
CONSUME_TOPIC    = os.environ.get('KAFKA_CONSUME_TOPIC',     'cmd.storage')
PRODUCE_TOPIC    = os.environ.get('KAFKA_PRODUCE_TOPIC',     'evt.storage.completed')
DLQ_TOPIC        = os.environ.get('KAFKA_DLQ_TOPIC',         'dead.letter.queue')
GROUP_ID         = os.environ.get('KAFKA_GROUP_ID',          'storage-group')
POSTGRES_URL     = os.environ.get('POSTGRES_URL', 'postgresql://faceuser:facepass@postgres:5432/facedb') \
                       .replace('postgresql+asyncpg://', 'postgresql://')
MINIO_ENDPOINT   = os.environ.get('MINIO_ENDPOINT',          'minio:9000')
MINIO_ACCESS     = os.environ.get('MINIO_ACCESS_KEY',        'minioadmin')
MINIO_SECRET     = os.environ.get('MINIO_SECRET_KEY',        'minioadmin')
MINIO_BUCKET     = os.environ.get('MINIO_BUCKET_PROCESSED',  'processed-images')
MINIO_SECURE     = os.environ.get('MINIO_SECURE',            'false').lower() == 'true'
PRESIGNED_EXPIRY = int(os.environ.get('MINIO_PRESIGNED_URL_EXPIRY', '3600'))


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


def process(msg, producer, minio_client):
    guid         = msg['guid_solicitud']
    id_solicitud = msg['id_solicitud']
    bucket       = msg.get('minio_bucket', MINIO_BUCKET)
    minio_path   = msg['minio_path']

    try:
        url = minio_client.presigned_get_object(
            bucket, minio_path, expires=timedelta(seconds=PRESIGNED_EXPIRY)
        )
    except Exception:
        url = f'http://{MINIO_ENDPOINT}/{bucket}/{minio_path}'

    conn = psycopg2.connect(POSTGRES_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE Solicitud SET Id_Fichero = %s WHERE Id_Solicitud = %s',
                (url, id_solicitud)
            )
        conn.commit()
    finally:
        conn.close()

    producer.send(PRODUCE_TOPIC, key=guid, value={
        'guid_solicitud': guid,
        'id_solicitud':   id_solicitud,
        'url_resultado':  url,
        'timestamp':      datetime.now(timezone.utc).isoformat(),
    })
    producer.flush()
    logger.info(f'[{guid}] Almacenado → {url}')


def main():
    logger.info('Iniciando Storage Service...')
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
                producer.send(DLQ_TOPIC, key='storage-error', value={
                    'service': 'storage-service', 'error': str(e), 'message': msg
                })
                producer.flush()
            except Exception:
                pass


if __name__ == '__main__':
    main()
