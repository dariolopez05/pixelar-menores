"""
Orquestador 4 — Cierre del pipeline
  Escucha:  evt.pixelation.completed
            evt.storage.completed
  Hace:     Actualiza Solicitud (timestamps pixelado, estado COMPLETED)
  Publica:  cmd.storage (tras pixelación)
"""
import os, json, logging, time
from datetime import datetime, timezone
import psycopg2
from kafka import KafkaConsumer, KafkaProducer

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('orchestrator-4')

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
                'evt.pixelation.completed',
                'evt.storage.completed',
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


def on_pixelation_completed(msg, producer):
    guid         = msg['guid_solicitud']
    id_solicitud = msg['id_solicitud']
    pixeladas    = msg.get('caras_pixeladas', 0)

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE Solicitud
                   SET Inicio_Pixelado      = %s,
                       Fin_Pixelado         = %s,
                       Num_Imagenes_Pixeladas = %s,
                       Estado               = 'STORAGE'
                   WHERE Id_Solicitud = %s""",
                (datetime.now(timezone.utc), datetime.now(timezone.utc), pixeladas, id_solicitud)
            )
        conn.commit()
    finally:
        conn.close()

    producer.send('cmd.storage', key=guid, value={
        'guid_solicitud': guid, 'id_solicitud': id_solicitud,
        'minio_bucket':   msg.get('minio_bucket', 'processed-images'),
        'minio_path':     msg.get('minio_path'),
    })
    producer.flush()
    logger.info(f'[{guid}] Pixelado OK ({pixeladas} caras) → cmd.storage')


def on_storage_completed(msg, producer):
    guid         = msg['guid_solicitud']
    id_solicitud = msg['id_solicitud']
    now          = datetime.now(timezone.utc)

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT Inicio_Solicitud FROM Solicitud WHERE Id_Solicitud = %s', (id_solicitud,))
            inicio = cur.fetchone()[0]
            cur.execute(
                """UPDATE Solicitud
                   SET Fin_Solicitud                  = %s,
                       Inicio_Almacenamiento_Solicitud = %s,
                       Fin_Almacenamiento_Solicitud    = %s,
                       Estado                          = 'COMPLETED'
                   WHERE Id_Solicitud = %s""",
                (now, now, now, id_solicitud)
            )
        conn.commit()
    finally:
        conn.close()
    logger.info(f'[{guid}] Solicitud completada')


def main():
    logger.info('Iniciando Orquestador 4...')
    producer, consumer = build_kafka('o4-group')

    for message in consumer:
        msg   = message.value
        topic = message.topic
        guid  = msg.get('guid_solicitud', '?')
        try:
            logger.info(f'[{guid}] Evento en {topic}')
            if topic == 'evt.pixelation.completed':
                on_pixelation_completed(msg, producer)
            elif topic == 'evt.storage.completed':
                on_storage_completed(msg, producer)
        except Exception as e:
            logger.error(f'[{guid}] Error: {e}', exc_info=True)
            producer.send(DLQ_TOPIC, key='o4-error', value={'service': 'orchestrator-4', 'error': str(e), 'message': msg})
            producer.flush()


if __name__ == '__main__':
    main()
