"""
Orquestador 2 — Tras detección de caras
  Escucha:  evt.face_detection.completed
  Hace:     Actualiza Solicitud (timestamps, num caras)
            Recorta cada cara y la sube a MinIO (face-crops)
            Inserta fila en Imagenes por cada cara (bbox + URL)
  Publica:  cmd.age_detection x N  (uno por cara)
            cmd.pixelation          (si no hay caras, lista vacía)
"""
import os
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO

import cv2
import numpy as np
import psycopg2
from kafka import KafkaConsumer, KafkaProducer
from minio import Minio

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('orchestrator-2')

KAFKA_BOOTSTRAP      = os.environ.get('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092')
POSTGRES_URL         = os.environ.get('POSTGRES_URL', 'postgresql://faceuser:facepass@postgres:5432/facedb')
DLQ_TOPIC            = os.environ.get('KAFKA_DLQ_TOPIC', 'dead.letter.queue')
MINIO_ENDPOINT       = os.environ.get('MINIO_ENDPOINT',   'minio:9000')
MINIO_ACCESS         = os.environ.get('MINIO_ACCESS_KEY', 'minioadmin')
MINIO_SECRET         = os.environ.get('MINIO_SECRET_KEY', 'minioadmin')
MINIO_BUCKET_RAW     = os.environ.get('MINIO_BUCKET_RAW',   'raw-images')
MINIO_BUCKET_FACES   = os.environ.get('MINIO_BUCKET_FACES', 'face-crops')
MINIO_SECURE         = os.environ.get('MINIO_SECURE', 'false').lower() == 'true'
PRESIGNED_EXPIRY     = int(os.environ.get('MINIO_PRESIGNED_URL_EXPIRY', '3600'))
CROP_MARGIN_RATIO    = float(os.environ.get('CROP_MARGIN_RATIO', '0.1'))


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


def _presigned(minio_client, bucket, path):
    try:
        return minio_client.presigned_get_object(bucket, path, expires=timedelta(seconds=PRESIGNED_EXPIRY))
    except Exception:
        return f'http://{MINIO_ENDPOINT}/{bucket}/{path}'


def _crop_face(img, x, y, w, h):
    h_img, w_img = img.shape[:2]
    margin = int(min(w, h) * CROP_MARGIN_RATIO)
    x1 = max(0, x - margin)
    y1 = max(0, y - margin)
    x2 = min(w_img, x + w + margin)
    y2 = min(h_img, y + h + margin)
    return img[y1:y2, x1:x2]


def main():
    logger.info('Iniciando Orquestador 2...')
    producer, consumer = build_kafka('o2-group')
    minio_client = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS, secret_key=MINIO_SECRET, secure=MINIO_SECURE)

    for message in consumer:
        msg  = message.value
        guid = msg.get('guid_solicitud', '?')
        try:
            id_solicitud = msg['id_solicitud']
            caras        = msg.get('caras', [])
            num_caras    = len(caras)
            bucket_raw   = msg.get('minio_bucket', MINIO_BUCKET_RAW)
            minio_path   = msg['minio_path']
            ext          = minio_path.rsplit('.', 1)[-1] if '.' in minio_path else 'jpg'

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
                         'AGE_DETECTION' if num_caras > 0 else 'PIXELATION',
                         id_solicitud),
                    )
                conn.commit()
            finally:
                conn.close()

            if num_caras == 0:
                producer.send('cmd.pixelation', key=guid, value={
                    'guid_solicitud':       guid,
                    'id_solicitud':         id_solicitud,
                    'minio_bucket_origen':  bucket_raw,
                    'minio_path_origen':    minio_path,
                    'minio_bucket_destino': 'processed-images',
                    'minio_path_destino':   minio_path,
                    'caras_menores':        [],
                })
                producer.flush()
                logger.info(f'[{guid}] Sin caras → cmd.pixelation (lista vacía)')
                continue

            # Descargar imagen original una sola vez
            response  = minio_client.get_object(bucket_raw, minio_path)
            img_bytes = response.read()
            response.close()
            response.release_conn()

            nparr = np.frombuffer(img_bytes, np.uint8)
            img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            for cara in caras:
                num_cara = cara['num_cara']
                x, y, w, h = cara['x'], cara['y'], cara['w'], cara['h']

                crop       = _crop_face(img, x, y, w, h)
                _, buf     = cv2.imencode(f'.{ext}', crop)
                crop_bytes = buf.tobytes()
                crop_path  = f'{guid}/cara_{num_cara}.{ext}'

                minio_client.put_object(
                    MINIO_BUCKET_FACES, crop_path,
                    BytesIO(crop_bytes), len(crop_bytes),
                    content_type='image/jpeg',
                )
                url_imagen = _presigned(minio_client, MINIO_BUCKET_FACES, crop_path)

                conn = get_db()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO Imagenes
                               (Id_Solicitud, Num_Cara, URL_Imagen, x, y, w, h, Estado)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, 'PENDING')
                               RETURNING Id_Imagen""",
                            (id_solicitud, num_cara, url_imagen, x, y, w, h),
                        )
                        id_imagen = cur.fetchone()[0]
                    conn.commit()
                finally:
                    conn.close()

                producer.send('cmd.age_detection', key=guid, value={
                    'guid_solicitud':    guid,
                    'id_solicitud':      id_solicitud,
                    'num_cara':          num_cara,
                    'id_imagen':         id_imagen,
                    'face_crops_bucket': MINIO_BUCKET_FACES,
                    'face_crops_path':   crop_path,
                    'minio_bucket':      bucket_raw,
                    'minio_path':        minio_path,
                    'num_total_caras':   num_caras,
                    'x': x, 'y': y, 'w': w, 'h': h,
                })
                logger.info(f'[{guid}] Cara {num_cara} recortada y subida → {crop_path}')

            producer.flush()
            logger.info(f'[{guid}] → {num_caras} mensajes cmd.age_detection publicados')

        except Exception as e:
            logger.error(f'[{guid}] Error: {e}', exc_info=True)
            producer.send(DLQ_TOPIC, key='o2-error', value={
                'service': 'orchestrator-2', 'error': str(e), 'message': msg,
            })
            producer.flush()


if __name__ == '__main__':
    main()
