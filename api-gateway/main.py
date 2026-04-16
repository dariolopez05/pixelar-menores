import os
import json
import uuid
import time
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
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
MINIO_BUCKET_RAW      = os.environ.get('MINIO_BUCKET_RAW', 'raw-images')
MINIO_BUCKET_PROCESSED = os.environ.get('MINIO_BUCKET_PROCESSED', 'processed-images')
MINIO_SECURE          = os.environ.get('MINIO_SECURE', 'false').lower() == 'true'
PRESIGNED_EXPIRY      = int(os.environ.get('MINIO_PRESIGNED_URL_EXPIRY', '3600'))

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


def _row_to_dict(row) -> dict:
    (id_sol, guid, id_fichero, inicio, fin,
     ini_caras, fin_caras, ini_alm, fin_alm,
     total, pixeladas, estado,
     ini_edad, fin_edad, ini_pix, fin_pix) = row

    def ms(a, b):
        return int((b - a).total_seconds() * 1000) if a and b else None

    return {
        'id_solicitud':            id_sol,
        'guid_solicitud':          guid,
        'estado':                  estado,
        'url_resultado':           id_fichero,
        'timestamps': {
            'inicio_solicitud':             inicio.isoformat() if inicio else None,
            'fin_solicitud':                fin.isoformat()    if fin    else None,
            'inicio_deteccion_caras':       ini_caras.isoformat() if ini_caras else None,
            'fin_deteccion_caras':          fin_caras.isoformat() if fin_caras else None,
            'inicio_edad':                  ini_edad.isoformat()  if ini_edad  else None,
            'fin_edad':                     fin_edad.isoformat()  if fin_edad  else None,
            'inicio_pixelado':              ini_pix.isoformat()   if ini_pix   else None,
            'fin_pixelado':                 fin_pix.isoformat()   if fin_pix   else None,
            'inicio_almacenamiento':        ini_alm.isoformat()   if ini_alm   else None,
            'fin_almacenamiento':           fin_alm.isoformat()   if fin_alm   else None,
        },
        'metricas': {
            'num_imagenes_total':           total,
            'num_imagenes_pixeladas':       pixeladas,
            'duracion_total_ms':            ms(inicio, fin),
            'duracion_deteccion_caras_ms':  ms(ini_caras, fin_caras),
            'duracion_edad_ms':             ms(ini_edad, fin_edad),
            'duracion_pixelado_ms':         ms(ini_pix, fin_pix),
            'duracion_almacenamiento_ms':   ms(ini_alm, fin_alm),
        },
    }


def _parse_minio_url(url: str) -> tuple[str, str]:
    """Extrae (bucket, object_path) de una URL MinIO/presignada.
    Ejemplo: http://minio:9000/processed-images/abc.jpg?X-Amz-...
             → ('processed-images', 'abc.jpg')
    """
    for bucket in [MINIO_BUCKET_PROCESSED, MINIO_BUCKET_RAW]:
        marker = f'/{bucket}/'
        if marker in url:
            path = url.split(marker, 1)[1].split('?')[0]
            return bucket, path
    return MINIO_BUCKET_PROCESSED, ''


def _regenerar_url(id_solicitud: int, guid: str, minio_path: str, bucket: str) -> str:
    """Genera una nueva URL presignada y la persiste en Solicitud.Id_Fichero."""
    try:
        url = minio_client.presigned_get_object(
            bucket, minio_path, expires=timedelta(seconds=PRESIGNED_EXPIRY)
        )
    except Exception:
        url = f'http://{MINIO_ENDPOINT}/{bucket}/{minio_path}'

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE Solicitud SET Id_Fichero = %s WHERE Id_Solicitud = %s',
                (url, id_solicitud),
            )
        conn.commit()
    finally:
        conn.close()

    logger.info(f'[{guid}] URL presignada renovada y guardada en BD')
    return url


def consultar_solicitud(guid: str) -> dict | None:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT Id_Solicitud, GUID_Solicitud, Id_Fichero,
                          Inicio_Solicitud, Fin_Solicitud,
                          Inicio_Deteccion_Caras, Fin_Deteccion_Caras,
                          Inicio_Almacenamiento_Solicitud, Fin_Almacenamiento_Solicitud,
                          Num_Imagenes_Total, Num_Imagenes_Pixeladas, Estado,
                          Inicio_Edad, Fin_Edad, Inicio_Pixelado, Fin_Pixelado
                   FROM Solicitud
                   WHERE GUID_Solicitud = %s""",
                (guid,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return None

    result = _row_to_dict(row)

    # Si está completada, regenerar URL presignada y persistirla en BD
    if result['estado'] == 'COMPLETED':
        stored_url = result.get('url_resultado') or ''
        bucket, minio_path = _parse_minio_url(stored_url)
        if minio_path:
            nueva_url = _regenerar_url(result['id_solicitud'], guid, minio_path, bucket)
            result['url_resultado'] = nueva_url

    return result


def listar_solicitudes(limit: int, offset: int, estado: str | None) -> list[dict]:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            if estado:
                cur.execute(
                    """SELECT Id_Solicitud, GUID_Solicitud, Id_Fichero,
                              Inicio_Solicitud, Fin_Solicitud,
                              Inicio_Deteccion_Caras, Fin_Deteccion_Caras,
                              Inicio_Almacenamiento_Solicitud, Fin_Almacenamiento_Solicitud,
                              Num_Imagenes_Total, Num_Imagenes_Pixeladas, Estado,
                              Inicio_Edad, Fin_Edad, Inicio_Pixelado, Fin_Pixelado
                       FROM Solicitud
                       WHERE Estado = %s
                       ORDER BY Id_Solicitud DESC
                       LIMIT %s OFFSET %s""",
                    (estado.upper(), limit, offset),
                )
            else:
                cur.execute(
                    """SELECT Id_Solicitud, GUID_Solicitud, Id_Fichero,
                              Inicio_Solicitud, Fin_Solicitud,
                              Inicio_Deteccion_Caras, Fin_Deteccion_Caras,
                              Inicio_Almacenamiento_Solicitud, Fin_Almacenamiento_Solicitud,
                              Num_Imagenes_Total, Num_Imagenes_Pixeladas, Estado,
                              Inicio_Edad, Fin_Edad, Inicio_Pixelado, Fin_Pixelado
                       FROM Solicitud
                       ORDER BY Id_Solicitud DESC
                       LIMIT %s OFFSET %s""",
                    (limit, offset),
                )
            rows = cur.fetchall()
            cur.execute('SELECT COUNT(*) FROM Solicitud' +
                        (' WHERE Estado = %s' if estado else ''),
                        (estado.upper(),) if estado else ())
            total = cur.fetchone()[0]
    finally:
        conn.close()

    return {'total': total, 'limit': limit, 'offset': offset,
            'solicitudes': [_row_to_dict(r) for r in rows]}


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


@app.get('/images')
def listar_imagenes(
    limit:  int = 20,
    offset: int = 0,
    estado: str | None = None,
):
    return listar_solicitudes(limit, offset, estado)


@app.get('/images/{guid}')
def get_resultado(guid: str):
    result = consultar_solicitud(guid)
    if result is None:
        raise HTTPException(status_code=404, detail='Solicitud no encontrada')
    return result
