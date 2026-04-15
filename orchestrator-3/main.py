"""
Orquestador 3 — Tras detección de edad
  Escucha:  evt.age_detection.completed
  Hace:     Actualiza Solicitud (timestamps edad)
  Publica:  cmd.pixelation  (si hay menores)
            cmd.storage     (si no hay menores)
"""
import os, json, logging, time
from datetime import datetime, timezone
import psycopg2
from kafka import KafkaConsumer, KafkaProducer

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('orchestrator-3')

KAFKA_BOOTSTRAP = os.environ.get('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092')
POSTGRES_URL    = os.environ.get('POSTGRES_URL', 'postgresql://faceuser:facepass@postgres:5432/facedb')
DLQ_TOPIC       = os.environ.get('KAFKA_DLQ_TOPIC', 'dead.letter.queue')


def build_kafka(group_id):
    for i in range(15):
        try:
            producer = KafkaProducer(
                bootstrap_servers=KAFKA_BOOTSTRAP,
                value_serializer=lambda v: json.dumps(v).encode(),
                key_serializer=lambda k: k.encode() if k else None,
            )
            consumer = KafkaConsumer(
                'evt.age_detection.completed',
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
    logger.info('Iniciando Orquestador 3...')
    producer, consumer = build_kafka('o3-group')

    for message in consumer:
        msg  = message.value
        guid = msg.get('guid_solicitud', '?')
        try:
            id_solicitud = msg['id_solicitud']
            caras        = msg.get('caras', [])
            menores      = [c for c in caras if c.get('es_menor')]
            logger.info(f'[{guid}] {len(menores)} menor(es) de {len(caras)} cara(s)')

            conn = get_db()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE Solicitud
                           SET Inicio_Edad = %s,
                               Fin_Edad    = %s,
                               Estado      = %s
                           WHERE Id_Solicitud = %s""",
                        (datetime.now(timezone.utc), datetime.now(timezone.utc),
                         'PIXELATION' if menores else 'STORAGE',
                         id_solicitud)
                    )
                conn.commit()
            finally:
                conn.close()

            if menores:
                minio_path = msg.get('minio_path', '')
                producer.send('cmd.pixelation', key=guid, value={
                    'guid_solicitud':       guid,
                    'id_solicitud':         id_solicitud,
                    'minio_bucket_origen':  msg.get('minio_bucket', 'raw-images'),
                    'minio_path_origen':    minio_path,
                    'minio_bucket_destino': 'processed-images',
                    'minio_path_destino':   minio_path,
                    'caras_menores':        menores,
                })
                logger.info(f'[{guid}] → cmd.pixelation ({len(menores)} menor(es))')
            else:
                producer.send('cmd.storage', key=guid, value={
                    'guid_solicitud': guid, 'id_solicitud': id_solicitud,
                    'minio_bucket':   msg.get('minio_bucket', 'raw-images'),
                    'minio_path':     msg.get('minio_path'),
                })
                logger.info(f'[{guid}] Sin menores → cmd.storage')
            producer.flush()

        except Exception as e:
            logger.error(f'[{guid}] Error: {e}', exc_info=True)
            producer.send(DLQ_TOPIC, key='o3-error', value={'service': 'orchestrator-3', 'error': str(e), 'message': msg})
            producer.flush()


if __name__ == '__main__':
    main()
