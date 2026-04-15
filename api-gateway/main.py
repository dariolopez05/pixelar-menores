import os
import json
import uuid
import time
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from io import BytesIO

import psycopg2
from fastapi import FastAPI, UploadFile, File, HTTPException
from kafka import KafkaProducer
from minio import Minio

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger('api-gateway')

# ── Config desde variables de entorno ───────────────────────
KAFKA_BOOTSTRAP  = os.environ.get('KAFKA_BOOTSTRAP_SERVERS', 'kafka:29092')
PRODUCE_TOPIC    = os.environ.get('KAFKA_PRODUCE_TOPIC',     'images.raw')
POSTGRES_URL     = os.environ.get('POSTGRES_URL', 'postgresql://faceuser:facepass@postgres:5432/facedb') \
                       .replace('postgresql+asyncpg://', 'postgresql://')
MINIO_ENDPOINT   = os.environ.get('MINIO_ENDPOINT',   'minio:9000')
MINIO_ACCESS     = os.environ.get('MINIO_ACCESS_KEY', 'minioadmin')
MINIO_SECRET     = os.environ.get('MINIO_SECRET_KEY', 'minioadmin')
MINIO_BUCKET_RAW = os.environ.get('MINIO_BUCKET_RAW', 'raw-images')
MINIO_SECURE     = os.environ.get('MINIO_SECURE', 'false').lower() == 'true'

# ── Clientes globales ────────────────────────────────────────
kafka_producer: KafkaProducer | None = None
minio_client:   Minio | None = None


def build_kafka_producer() -> KafkaProducer:
    for attempt in range(15):
        try:
            p = KafkaProducer(
                bootstrap_servers=KAFKA_BOOTSTRAP,
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                key_serializer=lambda k: k.encode('utf-8') if k else None,
            )
            logger.info('Kafka productor conectado')
            return p
        except Exception as exc:
            logger.warning(f'Kafka no disponible (intento {attempt + 1}): {exc}')
            time.sleep(4)
    raise RuntimeError('No se pudo conectar al productor Kafka')


def ensure_minio_buckets(client: Minio):
    for bucket in [MINIO_BUCKET_RAW, 'processed-images']:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
            logger.info(f'Bucket MinIO creado: {bucket}')
        else:
            logger.info(f'Bucket MinIO ya existe: {bucket}')


@asynccontextmanager
async def lifespan(app: FastAPI):
    global kafka_producer, minio_client
    kafka_producer = build_kafka_producer()
    minio_client   = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS,
                           secret_key=MINIO_SECRET, secure=MINIO_SECURE)
    ensure_minio_buckets(minio_client)
    logger.info('API Gateway listo')
    yield
    kafka_producer.close()


app = FastAPI(title='API Gateway — Pixelado de Menores', lifespan=lifespan)


# ── Helpers BD ───────────────────────────────────────────────

def get_db():
    return psycopg2.connect(POSTGRES_URL)


def insertar_solicitud(guid: str, filename: str) -> int:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO Solicitud
                   (GUID_Solicitud, Id_Fichero, Inicio_Solicitud, Estado)
                   VALUES (%s, %s, %s, 'PENDING')
                   RETURNING Id_Solicitud""",
                (guid, filename, datetime.now(timezone.utc)),
            )
            id_solicitud = cur.fetchone()[0]
        conn.commit()
        return id_solicitud
    finally:
        conn.close()


def consultar_solicitud(guid: str) -> dict | None:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT Estado, Fin_Solicitud, Inicio_Solicitud,
                          Num_Imagenes_Total, Num_Imagenes_Pixeladas
                   FROM Solicitud
                   WHERE GUID_Solicitud = %s""",
                (guid,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return None

    estado, fin, inicio, total, pixeladas = row
    result = {'guid_solicitud': guid, 'estado': estado}

    if estado == 'COMPLETED':
        duracion = int((fin - inicio).total_seconds() * 1000) if fin and inicio else None
        result.update({
            'duracion_total_ms':   duracion,
            'num_imagenes_total':  total,
            'num_imagenes_pixeladas': pixeladas,
        })

    return result


# ── Endpoints ────────────────────────────────────────────────

@app.get('/health')
def health():
    return {'status': 'ok'}


@app.post('/images', status_code=202)
def upload_image(file: UploadFile = File(...)):
    guid       = str(uuid.uuid4())
    ext        = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
    minio_path = f'{guid}.{ext}'

    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail='Fichero vacío')

    # 1. Subir imagen original a MinIO
    try:
        minio_client.put_object(
            MINIO_BUCKET_RAW, minio_path,
            BytesIO(content), len(content),
            content_type=file.content_type or 'image/jpeg',
        )
        logger.info(f'[{guid}] Imagen subida a MinIO → {minio_path}')
    except Exception as exc:
        logger.error(f'Error subiendo a MinIO: {exc}')
        raise HTTPException(status_code=500, detail='Error al almacenar la imagen')

    # 2. Registrar solicitud en PostgreSQL
    try:
        id_solicitud = insertar_solicitud(guid, file.filename)
        logger.info(f'[{guid}] Solicitud creada en BD con id={id_solicitud}')
    except Exception as exc:
        logger.error(f'Error insertando en BD: {exc}')
        raise HTTPException(status_code=500, detail='Error al registrar la solicitud')

    # 3. Publicar evento en Kafka
    evento = {
        'guid_solicitud':   guid,
        'id_solicitud':     id_solicitud,
        'minio_bucket':     MINIO_BUCKET_RAW,
        'minio_path':       minio_path,
        'fichero_original': file.filename,
        'timestamp':        datetime.now(timezone.utc).isoformat(),
    }
    kafka_producer.send(PRODUCE_TOPIC, key=guid, value=evento)
    kafka_producer.flush()
    logger.info(f'[{guid}] Evento publicado en {PRODUCE_TOPIC}')

    return {'guid_solicitud': guid, 'id_solicitud': id_solicitud, 'estado': 'PENDING'}


@app.get('/images/{guid}')
def get_resultado(guid: str):
    result = consultar_solicitud(guid)
    if result is None:
        raise HTTPException(status_code=404, detail='Solicitud no encontrada')
    return result
