"""
Orquestador 3 — Agregación de resultados de edad
  Escucha:  evt.age_detection.completed  (un mensaje por cara)
  Hace:     Acumula resultados hasta tener TODAS las caras de una solicitud
            Actualiza Imagenes (Edad, Es_Menor, Escore) por cada resultado
            Actualiza Solicitud (Inicio_Edad, Fin_Edad)
  Publica:  cmd.pixelation con lista de menores (puede ser vacía)
"""
import os
import json
import logging
import time
from datetime import datetime, timezone

import psycopg2
from kafka import KafkaConsumer, KafkaProducer

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('orchestrator-3')

KAFKA_BOOTSTRAP = os.environ.get('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092')
POSTGRES_URL    = os.environ.get('POSTGRES_URL', 'postgresql://faceuser:facepass@postgres:5432/facedb')
DLQ_TOPIC       = os.environ.get('KAFKA_DLQ_TOPIC', 'dead.letter.queue')

# Estado en memoria: {id_solicitud: {'total': N, 'raw_bucket': ..., 'raw_path': ..., 'resultados': [...]}}
pendientes: dict = {}


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
            id_solicitud    = msg['id_solicitud']
            num_cara        = msg['num_cara']
            id_imagen       = msg['id_imagen']
            edad_estimada   = msg.get('edad_estimada')
            es_menor        = msg['es_menor']
            escore          = msg.get('confianza_modelo', 0.0)
            num_total_caras = msg['num_total_caras']
            raw_bucket      = msg.get('minio_bucket', 'raw-images')
            raw_path        = msg.get('minio_path', '')
            x, y, w, h      = msg['x'], msg['y'], msg['w'], msg['h']

            now = datetime.now(timezone.utc)

            # Actualizar fila de Imagenes
            conn = get_db()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE Imagenes
                           SET Edad = %s, Es_Menor = %s, Escore = %s, Estado = 'PROCESSED'
                           WHERE Id_Imagen = %s""",
                        (round(edad_estimada) if edad_estimada is not None else None,
                         es_menor, round(escore, 4), id_imagen),
                    )
                conn.commit()
            finally:
                conn.close()

            # Inicializar acumulador para esta solicitud si es el primero
            if id_solicitud not in pendientes:
                pendientes[id_solicitud] = {
                    'total':      num_total_caras,
                    'raw_bucket': raw_bucket,
                    'raw_path':   raw_path,
                    'inicio':     now,
                    'resultados': [],
                }
                # Marcar Inicio_Edad en Solicitud
                conn = get_db()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE Solicitud SET Inicio_Edad = %s, Estado = 'AGE_DETECTION' WHERE Id_Solicitud = %s",
                            (now, id_solicitud),
                        )
                    conn.commit()
                finally:
                    conn.close()

            pendientes[id_solicitud]['resultados'].append({
                'num_cara': num_cara, 'es_menor': es_menor,
                'x': x, 'y': y, 'w': w, 'h': h,
            })

            recibidos = len(pendientes[id_solicitud]['resultados'])
            edad_str = f"{edad_estimada:.1f} años" if edad_estimada is not None else "?"
            logger.info(f'[{guid}] Cara {num_cara}: {edad_str} → {"MENOR" if es_menor else "ADULTO"} (escore={escore:.3f}) ({recibidos}/{num_total_caras})')

            if recibidos < num_total_caras:
                continue

            # Todas las caras procesadas
            entry    = pendientes.pop(id_solicitud)
            menores  = [r for r in entry['resultados'] if r['es_menor']]
            fin_edad = now

            conn = get_db()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE Solicitud
                           SET Fin_Edad = %s, Estado = %s
                           WHERE Id_Solicitud = %s""",
                        (fin_edad, 'PIXELATION', id_solicitud),
                    )
                conn.commit()
            finally:
                conn.close()

            producer.send('cmd.pixelation', key=guid, value={
                'guid_solicitud':       guid,
                'id_solicitud':         id_solicitud,
                'minio_bucket_origen':  entry['raw_bucket'],
                'minio_path_origen':    entry['raw_path'],
                'minio_bucket_destino': 'processed-images',
                'minio_path_destino':   entry['raw_path'],
                'caras_menores':        menores,
            })
            producer.flush()
            logger.info(f'[{guid}] Todas las caras procesadas → cmd.pixelation ({len(menores)} menor(es))')

        except Exception as e:
            logger.error(f'[{guid}] Error: {e}', exc_info=True)
            producer.send(DLQ_TOPIC, key='o3-error', value={
                'service': 'orchestrator-3', 'error': str(e), 'message': msg,
            })
            producer.flush()


if __name__ == '__main__':
    main()
