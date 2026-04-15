"""
Orquestador 2 — Tras detección de caras
  Escucha:  evt.face_detection.completed
  Hace:     Actualiza Solicitud (num caras, timestamps)
            Inserta filas en Imagenes
  Publica:  cmd.age_detection  (para las 3 instancias de DE)
            cmd.storage        (si no hay caras)
"""
import os, json, logging, time
from datetime import datetime, timezone
import psycopg2
from kafka import KafkaConsumer, KafkaProducer

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('orchestrator-2')

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
                'evt.face_detection.completed',
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
    logger.info('Iniciando Orquestador 2...')
    producer, consumer = build_kafka('o2-group')

    for message in consumer:
        msg  = message.value
        guid = msg.get('guid_solicitud', '?')
        try:
            id_solicitud = msg['id_solicitud']
            caras        = msg.get('caras', [])
            num_caras    = len(caras)
            logger.info(f'[{guid}] {num_caras} cara(s) detectada(s)')

            conn = get_db()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE Solicitud
                           SET Fin_Deteccion_Caras = %s,
                               Num_Imagenes_Total  = %s,
                               Estado = %s
                           WHERE Id_Solicitud = %s""",
                        (datetime.now(timezone.utc), num_caras,
                         'AGE_DETECTION' if num_caras > 0 else 'STORAGE',
                         id_solicitud)
                    )
                    for cara in caras:
                        cur.execute(
                            "INSERT INTO Imagenes (Id_Solicitud, Estado) VALUES (%s, 'PENDING')",
                            (id_solicitud,)
                        )
                conn.commit()
            finally:
                conn.close()

            if num_caras == 0:
                producer.send('cmd.storage', key=guid, value={
                    'guid_solicitud': guid, 'id_solicitud': id_solicitud,
                    'minio_bucket': msg.get('minio_bucket', 'raw-images'),
                    'minio_path':   msg.get('minio_path'),
                })
                logger.info(f'[{guid}] Sin caras → cmd.storage')
            else:
                producer.send('cmd.age_detection', key=guid, value={
                    'guid_solicitud': guid, 'id_solicitud': id_solicitud,
                    'minio_bucket':   msg.get('minio_bucket', 'raw-images'),
                    'minio_path':     msg.get('minio_path'),
                    'caras':          caras,
                })
                logger.info(f'[{guid}] → cmd.age_detection ({num_caras} caras)')
            producer.flush()

        except Exception as e:
            logger.error(f'[{guid}] Error: {e}', exc_info=True)
            producer.send(DLQ_TOPIC, key='o2-error', value={'service': 'orchestrator-2', 'error': str(e), 'message': msg})
            producer.flush()


if __name__ == '__main__':
    main()
