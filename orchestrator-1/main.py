"""
Orquestador 1 — Entrada del pipeline
  Escucha:  images.raw
  Hace:     Inserta Solicitud en BD con estado FACE_DETECTION
  Publica:  cmd.face_detection
"""
import os, json, logging, time
from datetime import datetime, timezone
import psycopg2
from kafka import KafkaConsumer, KafkaProducer

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('orchestrator-1')

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
                'images.raw',
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
    logger.info('Iniciando Orquestador 1...')
    producer, consumer = build_kafka('o1-group')

    for message in consumer:
        msg  = message.value
        guid = msg.get('guid_solicitud', '?')
        try:
            id_solicitud = msg['id_solicitud']
            logger.info(f'[{guid}] images.raw recibido → actualizando BD')

            conn = get_db()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE Solicitud
                           SET Estado = 'FACE_DETECTION',
                               Inicio_Deteccion_Caras = %s
                           WHERE Id_Solicitud = %s""",
                        (datetime.now(timezone.utc), id_solicitud)
                    )
                conn.commit()
            finally:
                conn.close()

            producer.send('cmd.face_detection', key=guid, value={
                'guid_solicitud': guid,
                'id_solicitud':   id_solicitud,
                'minio_bucket':   msg['minio_bucket'],
                'minio_path':     msg['minio_path'],
            })
            producer.flush()
            logger.info(f'[{guid}] → cmd.face_detection publicado')

        except Exception as e:
            logger.error(f'[{guid}] Error: {e}', exc_info=True)
            producer.send(DLQ_TOPIC, key='o1-error', value={'service': 'orchestrator-1', 'error': str(e), 'message': msg})
            producer.flush()


if __name__ == '__main__':
    main()
