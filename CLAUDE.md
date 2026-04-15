# Proyecto: Identificación y Pixelado de Rostros con Arquitectura Event-Driven

## Descripción general

Sistema distribuido basado en eventos (Event-Driven Architecture) que procesa imágenes para:
1. Detectar rostros
2. Estimar la edad de cada rostro
3. Pixelar automáticamente los rostros de personas menores de 18 años

La comunicación entre servicios es **completamente asíncrona** mediante Kafka. No hay llamadas HTTP directas entre microservicios.

---

## Stack tecnológico

| Componente | Tecnología |
|---|---|
| Mensajería | Apache Kafka (KRaft, sin Zookeeper) |
| Contenedores | Docker + Docker Compose |
| API | FastAPI (Python) |
| Face Detection | OpenCV Haar Cascades |
| Age Detection | DeepFace |
| Pixelation | OpenCV |
| Almacenamiento objetos | MinIO |
| Base de datos | PostgreSQL |
| Dataset | https://www.kaggle.com/datasets/frabbisw/facial-age |

---

## Contenedores (12 en total)

| Contenedor | Rol |
|---|---|
| `kafka` | Broker Kafka en modo KRaft (sin Zookeeper) |
| `postgres` | Base de datos PostgreSQL |
| `minio` | Almacenamiento de objetos (S3-compatible) |
| `api-gateway` | Punto de entrada HTTP (FastAPI) |
| `orchestrator-1` | Gestiona entrada del pipeline |
| `orchestrator-2` | Gestiona post-detección de caras |
| `orchestrator-3` | Gestiona post-detección de edad |
| `orchestrator-4` | Cierra el pipeline |
| `face-detection` | Detecta todas las caras de una imagen |
| `age-detection` | Estima la edad de cada cara |
| `pixelation` | Pixela las caras de menores |
| `storage-service` | Genera URL final y cierra la solicitud |

---

## Servicios

### 1. API Gateway
- **Rol:** Punto de entrada HTTP para el cliente
- **Produce:** `images.raw`
- **Consume:** consulta BD para devolver resultado
- **Tecnología:** FastAPI
- **Endpoints:**
  - `POST /images` — recibe imagen, la sube a MinIO como `raw-images/{guid}.{ext}`, inserta en `Solicitud`, publica en `images.raw`, devuelve GUID
  - `GET /images/{guid}` — consulta estado y métricas, o 404 si no existe
  - `GET /health` — health check
- **Nota:** crea los buckets `raw-images` y `processed-images` en MinIO al arrancar si no existen

### 2. Orquestadores (x4)

#### Orquestador 1
- **Escucha:** `images.raw`
- **Hace:** actualiza `Solicitud` (Estado → `FACE_DETECTION`, `Inicio_Deteccion_Caras`)
- **Publica:** `cmd.face_detection`

#### Orquestador 2
- **Escucha:** `evt.face_detection.completed`
- **Hace:** actualiza `Solicitud` (`Fin_Deteccion_Caras`, `Num_Imagenes_Total`), inserta filas en `Imagenes`
- **Publica:** `cmd.age_detection` (si hay caras) o `cmd.storage` (si no hay caras)

#### Orquestador 3
- **Escucha:** `evt.age_detection.completed`
- **Hace:** actualiza `Solicitud` (`Inicio_Edad`, `Fin_Edad`, Estado)
- **Publica:** `cmd.pixelation` (si hay menores) o `cmd.storage` (si no hay menores)

#### Orquestador 4
- **Escucha:** `evt.pixelation.completed` y `evt.storage.completed`
- **Hace (pixelation):** actualiza `Solicitud` (`Inicio_Pixelado`, `Fin_Pixelado`, `Num_Imagenes_Pixeladas`) → publica `cmd.storage`
- **Hace (storage):** marca `Solicitud` como `COMPLETED` con `Fin_Solicitud` y timestamps de almacenamiento

### 3. Face Detection Service
- **1 solo contenedor**
- **Consume:** `cmd.face_detection`
- **Produce:** `evt.face_detection.completed`
- **Función:** detecta todas las caras de la imagen y devuelve sus bounding boxes `[{num_cara, x, y, w, h}]`
- **Tecnología:** OpenCV Haar Cascades (`haarcascade_frontalface_default.xml`)

### 4. Age Detection Service
- **1 solo contenedor**
- **Consume:** `cmd.age_detection`
- **Produce:** `evt.age_detection.completed`
- **Función:** estima la edad de cada cara y clasifica como `es_menor` si `edad < 18`
- **Tecnología:** DeepFace (`actions=['age']`, `enforce_detection=False`)

### 5. Pixelation Service
- **Consume:** `cmd.pixelation`
- **Produce:** `evt.pixelation.completed`
- **Función:** pixela las regiones de los menores (bloque 20px), guarda en `processed-images/{guid}.{ext}`
- **Tecnología:** OpenCV (resize down + resize up con `INTER_NEAREST`)

### 6. Storage Service
- **Consume:** `cmd.storage`
- **Produce:** `evt.storage.completed`
- **Función:** genera URL presignada de MinIO (1h), actualiza `Solicitud.Id_Fichero`, publica evento de cierre

### 7. MinIO
- Almacenamiento de objetos
- Bucket `raw-images` — imágenes originales: clave `{guid}.{ext}`
- Bucket `processed-images` — imágenes pixeladas: clave `{guid}.{ext}` (mismo nombre, distinto bucket)
- El GUID es el identificador único del objeto en ambos buckets

---

## Topics de Kafka

| Topic | Productor | Consumidor |
|---|---|---|
| `images.raw` | API Gateway | Orquestador 1 |
| `cmd.face_detection` | Orquestador 1 | Face Detection |
| `evt.face_detection.completed` | Face Detection | Orquestador 2 |
| `cmd.age_detection` | Orquestador 2 | Age Detection |
| `evt.age_detection.completed` | Age Detection | Orquestador 3 |
| `cmd.pixelation` | Orquestador 3 | Pixelation |
| `evt.pixelation.completed` | Pixelation | Orquestador 4 |
| `cmd.storage` | Orquestador 3 / Orquestador 4 | Storage Service |
| `evt.storage.completed` | Storage Service | Orquestador 4 |
| `dead.letter.queue` | Cualquier servicio en error | — |

> Los topics se crean automáticamente (`KAFKA_AUTO_CREATE_TOPICS_ENABLE: true`). No hay contenedor `kafka-init`.

---

## Flujo conceptual

```
1.  Cliente        → POST /images              → API Gateway
2.  API Gateway    → sube imagen a MinIO        → raw-images/{guid}.ext
                   → publica                   → images.raw
3.  Orquestador 1  → consume images.raw
                   → actualiza BD              → Estado = FACE_DETECTION
                   → publica                   → cmd.face_detection
4.  Face Det.      → descarga raw-images/{guid}.ext
                   → detecta TODAS las caras
                   → publica                   → evt.face_detection.completed
5.  Orquestador 2  → actualiza BD, inserta Imagenes
                   → publica                   → cmd.age_detection (si hay caras)
                                               → cmd.storage (si no hay caras)
6.  Age Det.       → descarga imagen, recorta cada cara
                   → estima edad con DeepFace
                   → publica                   → evt.age_detection.completed
7.  Orquestador 3  → actualiza BD
      ├─ menores   → publica                   → cmd.pixelation
      └─ sin men.  → publica                   → cmd.storage
8.  Pixelation     → descarga raw-images/{guid}.ext
                   → pixela caras de menores
                   → sube a processed-images/{guid}.ext
                   → publica                   → evt.pixelation.completed
9.  Orquestador 4  → actualiza BD (timestamps pixelado)
                   → publica                   → cmd.storage
10. Storage        → genera URL presignada de MinIO
                   → actualiza Solicitud.Id_Fichero
                   → publica                   → evt.storage.completed
11. Orquestador 4  → marca Estado = COMPLETED
12. Cliente        → GET /images/{guid}        → API Gateway devuelve estado y métricas
```

---

## Esquema de base de datos

```sql
CREATE TABLE IF NOT EXISTS Solicitud (
    Id_Solicitud                    SERIAL PRIMARY KEY,
    GUID_Solicitud                  VARCHAR(255),
    Id_Fichero                      VARCHAR(255),   -- URL presignada del resultado en MinIO
    Inicio_Solicitud                TIMESTAMP,
    Fin_Solicitud                   TIMESTAMP,
    Inicio_Deteccion_Caras          TIMESTAMP,
    Fin_Deteccion_Caras             TIMESTAMP,
    Inicio_Almacenamiento_Solicitud TIMESTAMP,
    Fin_Almacenamiento_Solicitud    TIMESTAMP,
    Num_Imagenes_Total              INT,
    Num_Imagenes_Pixeladas          INT,
    Estado                          VARCHAR(50),    -- PENDING, FACE_DETECTION, AGE_DETECTION, PIXELATION, STORAGE, COMPLETED, ERROR
    Inicio_Edad                     TIMESTAMP,
    Fin_Edad                        TIMESTAMP,
    Inicio_Pixelado                 TIMESTAMP,
    Fin_Pixelado                    TIMESTAMP
);

CREATE TABLE IF NOT EXISTS Imagenes (
    Id_Imagen    SERIAL PRIMARY KEY,
    Id_Solicitud INT,
    Estado       VARCHAR(50),
    FOREIGN KEY (Id_Solicitud) REFERENCES Solicitud(Id_Solicitud)
);
```

---

## Estados de una solicitud

```
PENDING → FACE_DETECTION → AGE_DETECTION → PIXELATION → STORAGE → COMPLETED
                                        ↘ (sin menores o sin caras)
                                              STORAGE → COMPLETED
```

---

## Estructura de carpetas

```
proyecto_pixelar-menores/
├── docker-compose.yml
├── CLAUDE.md
├── INSTRUCCIONES.md
├── .env
├── scripts/
│   ├── fase1-infra-up.bat
│   ├── fase2-api-gateway-up.bat
│   ├── fase3-orchestrator-up.bat
│   ├── fase4-face-detection-up.bat
│   ├── fase5-age-detection-up.bat
│   ├── fase6-pixelation-up.bat
│   ├── fase7-storage-up.bat
│   ├── status.bat
│   ├── logs.bat
│   ├── down.bat
│   ├── reset.bat
│   ├── test-pipeline.bat
│   └── README.md
├── api-gateway/
│   ├── Dockerfile
│   ├── main.py
│   └── requirements.txt
├── orchestrator-1/
│   ├── Dockerfile
│   ├── main.py
│   └── requirements.txt
├── orchestrator-2/
│   ├── Dockerfile
│   ├── main.py
│   └── requirements.txt
├── orchestrator-3/
│   ├── Dockerfile
│   ├── main.py
│   └── requirements.txt
├── orchestrator-4/
│   ├── Dockerfile
│   ├── main.py
│   └── requirements.txt
├── face-detection/
│   ├── Dockerfile
│   ├── main.py
│   └── requirements.txt
├── age-detection/
│   ├── Dockerfile
│   ├── main.py
│   ├── model/
│   └── requirements.txt
├── pixelation/
│   ├── Dockerfile
│   ├── main.py
│   └── requirements.txt
├── storage-service/
│   ├── Dockerfile
│   ├── main.py
│   └── requirements.txt
└── db/
    └── init.sql
```

---

## Notas de implementación

- Las imágenes **no viajan dentro de los mensajes Kafka**. Se guardan en MinIO y los eventos transportan solo la referencia (bucket + clave).
- El **GUID** es el identificador único de cada solicitud y de su imagen en MinIO. La clave del objeto es `{guid}.{ext}` en ambos buckets.
- El API Gateway crea los buckets de MinIO al arrancar si no existen (no hay contenedor `minio-init`).
- Kafka usa modo **KRaft** (sin Zookeeper). Los topics se crean automáticamente.
- Cada servicio Python usa `kafka-python` como cliente Kafka y `psycopg2-binary` para PostgreSQL.
- Los orquestadores son los únicos servicios que escriben en la tabla `Solicitud`.

---

## Gestión de errores

- **Dead-letter queue:** mensajes fallidos se publican en `dead.letter.queue`
- **Idempotencia:** cada servicio es idempotente (mismo GUID = mismo resultado)
- **Reintentos de conexión:** cada servicio reintenta conectar a Kafka hasta 15 veces con espera de 5s
