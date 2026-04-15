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
| Mensajería | Apache Kafka + Zookeeper |
| Contenedores | Docker + Docker Compose |
| API | FastAPI (Python) |
| Face Detection | OpenCV / YOLO / Dlib |
| Age Detection | PyTorch / TensorFlow (ResNet o similar) |
| Pixelation | OpenCV |
| Almacenamiento objetos | MinIO (S3-compatible) |
| Base de datos | SQL (PostgreSQL o MySQL) |
| Dataset | https://www.kaggle.com/datasets/frabbisw/facial-age |

---

## Servicios

### 1. API Gateway
- **Rol:** Punto de entrada HTTP para el cliente
- **Produce:** `images.raw`
- **Consume:** consulta BD y MinIO para devolver resultado
- **Tecnología:** FastAPI
- **Endpoints:**
  - `POST /images` — recibe imagen, publica en `images.raw`, devuelve GUID
  - `GET /images/{guid}` — consulta estado y devuelve imagen procesada o 404

### 2. Orquestador
- **Rol:** Coordinador del workflow. No procesa imágenes, solo gestiona estado y decide el siguiente paso
- **Consume:** todos los eventos `evt.*` y `images.raw`
- **Produce:** todos los comandos `cmd.*`
- **Responsabilidades:**
  - Guardar estado de cada solicitud en BD
  - Decidir el siguiente servicio según el estado actual
  - Gestionar errores, reintentos y rutas alternativas
  - Marcar solicitudes como COMPLETED

### 3. Face Detection Service
- **Consume:** `cmd.face_detection`
- **Produce:** `evt.face_detection.completed`
- **Función:** Detectar rostros y generar bounding boxes
- **Tecnología:** OpenCV, YOLO o Dlib

### 4. Age Detection Service
- **Consume:** `cmd.age_detection`
- **Produce:** `evt.age_detection.completed`
- **Función:** Estimar edad de cada rostro y clasificar como `<18` o `>=18`
- **Tecnología:** PyTorch / TensorFlow (ResNet o similar)

### 5. Pixelation Service
- **Consume:** `cmd.pixelation`
- **Produce:** `evt.pixelation.completed`
- **Función:** Pixelar las regiones de bounding box de rostros menores
- **Tecnología:** OpenCV

### 6. Storage / Result Service
- **Consume:** `cmd.storage`
- **Produce:** `evt.storage.completed`
- **Función:** Guardar imagen final en MinIO, actualizar BD con URL y timestamps

### 7. MinIO
- Almacenamiento de objetos compatible con AWS S3
- Contiene imágenes originales y procesadas

---

## Topics de Kafka

| Topic | Productor | Consumidor |
|---|---|---|
| `images.raw` | API Gateway | Orquestador |
| `cmd.face_detection` | Orquestador | Face Detection Service |
| `evt.face_detection.completed` | Face Detection Service | Orquestador |
| `cmd.age_detection` | Orquestador | Age Detection Service |
| `evt.age_detection.completed` | Age Detection Service | Orquestador |
| `cmd.pixelation` | Orquestador | Pixelation Service |
| `evt.pixelation.completed` | Pixelation Service | Orquestador |
| `cmd.storage` | Orquestador | Storage Service |
| `evt.storage.completed` | Storage Service | Orquestador |

---

## Flujo conceptual

```
1.  Cliente       → POST /images           → API Gateway
2.  API Gateway   → publica                → images.raw
3.  Orquestador   → consume images.raw     → guarda estado inicial en BD
                  → publica               → cmd.face_detection
4.  Face Det.     → consume cmd.face_detection
                  → publica               → evt.face_detection.completed (con bounding boxes)
5.  Orquestador   → actualiza estado BD
                  → publica               → cmd.age_detection
6.  Age Det.      → consume cmd.age_detection
                  → publica               → evt.age_detection.completed (con edades estimadas)
7.  Orquestador   → ¿hay menores?
      ├─ Sí       → publica               → cmd.pixelation
      └─ No       → publica               → cmd.storage
8.  Pixelation    → consume cmd.pixelation
                  → publica               → evt.pixelation.completed (imagen modificada)
9.  Orquestador   → publica               → cmd.storage
10. Storage       → guarda en MinIO, actualiza BD
                  → publica               → evt.storage.completed
11. Orquestador   → marca solicitud como COMPLETED en BD
12. Cliente       → GET /images/{guid}    → API Gateway devuelve URL de MinIO o 404
```

---

## Contratos de eventos (JSON schemas)

### `images.raw`
```json
{
  "guid": "uuid-string",
  "timestamp": "ISO8601",
  "filename": "imagen.jpg",
  "image_data": "base64-encoded-bytes"
}
```

### `cmd.face_detection`
```json
{
  "guid": "uuid-string",
  "timestamp": "ISO8601",
  "image_ref": "minio-object-key"
}
```

### `evt.face_detection.completed`
```json
{
  "guid": "uuid-string",
  "timestamp": "ISO8601",
  "faces": [
    { "face_id": 0, "bbox": { "x": 10, "y": 20, "w": 50, "h": 60 } }
  ],
  "processing_time_ms": 120
}
```

### `cmd.age_detection`
```json
{
  "guid": "uuid-string",
  "timestamp": "ISO8601",
  "image_ref": "minio-object-key",
  "faces": [ { "face_id": 0, "bbox": { "x": 10, "y": 20, "w": 50, "h": 60 } } ]
}
```

### `evt.age_detection.completed`
```json
{
  "guid": "uuid-string",
  "timestamp": "ISO8601",
  "faces": [
    { "face_id": 0, "bbox": { "x": 10, "y": 20, "w": 50, "h": 60 }, "estimated_age": 15, "is_minor": true }
  ],
  "has_minors": true,
  "processing_time_ms": 340
}
```

### `cmd.pixelation`
```json
{
  "guid": "uuid-string",
  "timestamp": "ISO8601",
  "image_ref": "minio-object-key",
  "faces_to_pixelate": [
    { "face_id": 0, "bbox": { "x": 10, "y": 20, "w": 50, "h": 60 } }
  ]
}
```

### `evt.pixelation.completed`
```json
{
  "guid": "uuid-string",
  "timestamp": "ISO8601",
  "image_ref": "minio-object-key-pixelated",
  "processing_time_ms": 80
}
```

### `cmd.storage`
```json
{
  "guid": "uuid-string",
  "timestamp": "ISO8601",
  "image_ref": "minio-object-key",
  "metadata": {
    "total_faces": 2,
    "pixelated_faces": 1
  }
}
```

### `evt.storage.completed`
```json
{
  "guid": "uuid-string",
  "timestamp": "ISO8601",
  "result_url": "http://minio:9000/bucket/processed/uuid.jpg",
  "processing_time_ms": 50
}
```

---

## Esquema de base de datos

```sql
CREATE TABLE Solicitud (
    Id_Solicitud            INT PRIMARY KEY AUTO_INCREMENT,
    GUID_Solicitud          VARCHAR(255) UNIQUE NOT NULL,
    Id_Fichero              VARCHAR(255),
    Inicio_Solicitud        DATETIME,
    Fin_Solicitud           DATETIME,
    Inicio_Deteccion_Caras  DATETIME,
    Fin_Deteccion_Caras     DATETIME,
    Inicio_Almacenamiento_Solicitud DATETIME,
    Fin_Almacenamiento_Solicitud    DATETIME,
    Num_Imagenes_Total      INT DEFAULT 0,
    Num_Imagenes_Pixeladas  INT DEFAULT 0,
    Estado                  VARCHAR(50)  -- PENDING, FACE_DETECTION, AGE_DETECTION, PIXELATION, STORAGE, COMPLETED, ERROR
);

CREATE TABLE Imagenes (
    Id_Imagen       INT PRIMARY KEY AUTO_INCREMENT,
    Id_Solicitud    INT NOT NULL,
    Inicio_Edad     DATETIME,
    Fin_edad        DATETIME,
    Inicio_Pixelado DATETIME,
    Fin_Pixelado    DATETIME,
    Estado          VARCHAR(50),
    FOREIGN KEY (Id_Solicitud) REFERENCES Solicitud(Id_Solicitud)
);
```

---

## Estados de una solicitud

```
PENDING → FACE_DETECTION → AGE_DETECTION → PIXELATION → STORAGE → COMPLETED
                                        ↘ (sin menores)          ↗
                                          ──────────────────────
                                                STORAGE
```

En caso de error en cualquier paso: estado `ERROR` con posibilidad de reintento.

---

## Métricas de rendimiento

Cada servicio debe registrar y loguear:
- `processing_time_ms` — tiempo de procesamiento del evento
- Latencia end-to-end (calculada en el orquestador: `Fin_Solicitud - Inicio_Solicitud`)
- Throughput (opcional): eventos procesados por segundo

---

## Gestión de errores

- **Dead-letter queue:** mensajes que fallan N veces se publican en `evt.*.failed`
- **Reintentos:** el orquestador reenvía el comando hasta 3 veces antes de marcar ERROR
- **Timeout:** si un servicio no responde en X segundos, el orquestador actúa
- **Idempotencia:** cada servicio debe ser idempotente (mismo GUID = mismo resultado)

---

## Estructura de carpetas esperada

```
proyecto_pixelar-menores/
├── docker-compose.yml
├── CLAUDE.md
├── README.md
├── api-gateway/
│   ├── Dockerfile
│   ├── main.py
│   └── requirements.txt
├── orchestrator/
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

- Las imágenes **no viajan dentro de los mensajes Kafka** (demasiado grandes). Se guardan en MinIO desde el API Gateway y los eventos transportan solo la referencia (`minio-object-key`).
- El API Gateway sube la imagen original a MinIO al recibirla, antes de publicar en `images.raw`.
- Cada servicio Python usa `confluent-kafka` o `kafka-python` como cliente Kafka.
- El orquestador es el único servicio que escribe en la tabla `Solicitud`. Los demás solo leen sus comandos de Kafka.
