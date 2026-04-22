"""
Orquestador 4 — Cierre del pipeline
  Escucha:  evt.pixelation.completed
  Hace:     Genera URL presignada de la imagen procesada en MinIO
            Actualiza Solicitud con URL, timestamps y Estado = COMPLETED
"""
import os
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import psycopg2
from kafka import KafkaConsumer, KafkaProducer
from minio import Minio

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('orchestrator-4')

KAFKA_BOOTSTRAP  = os.environ.get('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092')
POSTGRES_URL     = os.environ.get('POSTGRES_URL', 'postgresql://faceuser:facepass@postgres:5432/facedb')
DLQ_TOPIC        = os.environ.get('KAFKA_DLQ_TOPIC', 'dead.letter.queue')
MINIO_ENDPOINT   = os.environ.get('MINIO_ENDPOINT',   'minio:9000')
MINIO_ACCESS     = os.environ.get('MINIO_ACCESS_KEY', 'minioadmin')
MINIO_SECRET     = os.environ.get('MINIO_SECRET_KEY', 'minioadmin')
MINIO_SECURE     = os.environ.get('MINIO_SECURE', 'false').lower() == 'true'
PRESIGNED_EXPIRY = int(os.environ.get('MINIO_PRESIGNED_URL_EXPIRY', '3600'))


def build_kafka(group_id):
    for i in range(15):
        try:
            producer = KafkaProducer(
                bootstrap_servers=KAFKA_BOOTSTRAP,
                value_serializer=lambda v: json.dumps(v).encode(),
                key_serializer=lambda k: k.encode() if k else None,
            )
            consumer = KafkaConsumer(
                'evt.pixelation.completed',
                bootstrap_servers=KAFKA_BOOTSTRAP,
                group_id=group_id,
                auto_offset_reset='earliest',
                enable_auto_commit=True,
                value_deserializer=lambda v: json.loads(v.decode()),
            )
            return producer, consumer
        except Exception as e:
            logger.warning(f'Kafka no disponible (intento {i+1}): {e}')
            time.sleep(5)
    raise RuntimeError('No se pudo conectar a Kafka')


def get_db():
    return psycopg2.connect(POSTGRES_URL)


def main():
    logger.info('Iniciando Orquestador 4...')
    producer, consumer = build_kafka('o4-group')
    minio_client = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS, secret_key=MINIO_SECRET, secure=MINIO_SECURE)

    for message in consumer:
        msg  = message.value
        guid = msg.get('guid_solicitud', '?')
        try:
            id_solicitud  = msg['id_solicitud']
            bucket_dst    = msg.get('minio_bucket', 'processed-images')
            path_dst      = msg['minio_path']
            caras_pixeladas = msg.get('caras_pixeladas', 0)

            now = datetime.now(timezone.utc)

            # Generar URL presignada de la imagen procesada
            try:
                url_resultado = minio_client.presigned_get_object(
                    bucket_dst, path_dst, expires=timedelta(seconds=PRESIGNED_EXPIRY)
                )
            except Exception:
                url_resultado = f'http://{MINIO_ENDPOINT}/{bucket_dst}/{path_dst}'

            conn = get_db()
            try:
                with conn.cursor() as cur:
                    cur.execute('SELECT Inicio_Pixelado FROM Solicitud WHERE Id_Solicitud = %s', (id_solicitud,))
                    row = cur.fetchone()
                    ini_pixelado = row[0] if row and row[0] else now

                    cur.execute(
                        """UPDATE Solicitud
                           SET Id_Fichero                      = %s,
                               Fin_Solicitud                   = %s,
                               Inicio_Pixelado                 = %s,
                               Fin_Pixelado                    = %s,
                               Inicio_Almacenamiento_Solicitud = %s,
                               Fin_Almacenamiento_Solicitud    = %s,
                               Num_Imagenes_Pixeladas          = %s,
                               Estado                          = 'COMPLETED'
                           WHERE Id_Solicitud = %s""",
                        (url_resultado, now, ini_pixelado, now, now, now, caras_pixeladas, id_solicitud),
                    )
                conn.commit()
            finally:
                conn.close()

            logger.info(f'[{guid}] Solicitud completada — {caras_pixeladas} cara(s) pixelada(s)')

        except Exception as e:
            logger.error(f'[{guid}] Error: {e}', exc_info=True)
            producer.send(DLQ_TOPIC, key='o4-error', value={
                'service': 'orchestrator-4', 'error': str(e), 'message': msg,
            })
            producer.flush()


if __name__ == '__main__':
    main()
