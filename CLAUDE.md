# Proyecto: Identificación y Pixelado de Rostros con Arquitectura Event-Driven

## Descripción general

Sistema distribuido basado en eventos (Event-Driven Architecture) que procesa imágenes para:
1. Detectar rostros
2. Estimar la edad de cada rostro de forma individual
3. Pixelar automáticamente los rostros de personas menores de 18 años

La comunicación entre servicios es **completamente asíncrona** mediante Kafka. No hay llamadas HTTP directas entre microservicios.

---

## Stack tecnológico

| Componente | Tecnología |
|---|---|
| Mensajería | Apache Kafka (KRaft, sin Zookeeper) |
| Contenedores | Docker + Docker Compose |
| API | FastAPI (Python) |
| Face Detection | YOLOv8 (`yolov8n-face.pt`) |
| Age Detection | MobileNetV2 (clasificador binario menor/adulto, custom TensorFlow/Keras) |
| Pixelation | OpenCV |
| Almacenamiento objetos | MinIO |
| Base de datos | PostgreSQL |
| Dataset | https://www.kaggle.com/datasets/frabbisw/facial-age |

---

## Contenedores (11 en total)

| Contenedor | Rol |
|---|---|
| `kafka` | Broker Kafka en modo KRaft (sin Zookeeper) |
| `postgres` | Base de datos PostgreSQL |
| `minio` | Almacenamiento de objetos (S3-compatible) |
| `api-gateway` | Punto de entrada HTTP (FastAPI) |
| `orchestrator-1` | Inicia el pipeline y lanza detección de caras |
| `orchestrator-2` | Recibe caras detectadas, recorta cada cara y lanza detección de edad individual |
| `orchestrator-3` | Agrega resultados de edad y decide si pixelar |
| `orchestrator-4` | Finaliza el pipeline y genera URL de resultado |
| `face-detection` | Detecta todas las caras de una imagen |
| `age-detection` | Estima la edad de una cara individual |
| `pixelation` | Pixela las caras de menores y guarda imagen procesada |

---

## Servicios

### 1. API Gateway
- **Rol:** Punto de entrada HTTP para el cliente
- **Produce:** `images.raw` (T1)
- **Consume:** consulta BD para devolver resultado
- **Tecnología:** FastAPI
- **Endpoints:**
  - `POST /images` — recibe imagen, la sube a MinIO como `raw-images/{guid}.{ext}`, inserta en `Solicitud` (Estado = `PENDING`, `Inicio_Solicitud`), publica en `images.raw`, devuelve GUID
  - `GET /images/{guid}` — consulta estado y métricas, o 404 si no existe
  - `GET /health` — health check
- **Nota:** crea los buckets `raw-images`, `face-crops` y `processed-images` en MinIO al arrancar si no existen

---

### 2. Orquestadores (x4)

#### Orquestador 1
- **Escucha:** `images.raw` (T1)
- **Qué hace:** Inicia el proceso de la imagen y lanza la detección de caras
- **BD — qué guarda:**
  - En `Solicitud`: actualiza `Inicio_Deteccion_Caras`, Estado → `FACE_DETECTION`
  - Registra la llegada de la imagen y el momento en que empieza el pipeline
- **MinIO:** no interactúa directamente
- **Publica:** `cmd.face_detection` (T2) con `{guid, ext}`

#### Orquestador 2
- **Escucha:** `evt.face_detection.completed` (T3)
- **Qué hace:** Recibe las caras detectadas y separa cada cara para procesarlas individualmente
  1. Descarga la imagen cruda de MinIO (`raw-images/{guid}.{ext}`)
  2. Recorta cada cara usando sus bounding boxes
  3. Sube cada recorte a MinIO como `face-crops/{guid}/cara_{num_cara}.{ext}`
  4. Envía un mensaje `cmd.age_detection` (T4) por cada cara
- **BD — qué guarda:**
  - En `Solicitud`: actualiza `Fin_Deteccion_Caras`, `Num_Imagenes_Total`
  - En `Imagenes`: inserta una fila por cara con `Num_Cara`, `x`, `y`, `w`, `h`
- **MinIO:** lee `raw-images/`, escribe en `face-crops/`
- **Publica:** N mensajes `cmd.age_detection` (T4), uno por cara (si N = 0, publica `cmd.pixelation` con lista vacía)

#### Orquestador 3
- **Escucha:** `evt.age_detection.completed` (T5) — recibe un mensaje por cara
- **Qué hace:** Acumula los resultados de edad hasta tener todas las caras procesadas; entonces decide si hay que pixelar o no
  - Compara el número de resultados recibidos con `Num_Imagenes_Total` de la `Solicitud`
  - Cuando todos los resultados llegaron, publica `cmd.pixelation` con la lista de caras menores (puede ser vacía)
- **BD — qué guarda:**
  - En `Imagenes`: actualiza `Edad` y `Es_Menor` de cada cara según llegan los resultados
  - En `Solicitud`: actualiza `Inicio_Edad` (primer resultado) y `Fin_Edad` (último resultado)
- **MinIO:** no interactúa directamente
- **Publica:** `cmd.pixelation` (T6) con `{guid, ext, lista_menores: [{num_cara, x, y, w, h}]}`

#### Orquestador 4
- **Escucha:** `evt.pixelation.completed` (T7)
- **Qué hace:** Finaliza todo el proceso y deja la imagen lista para el usuario
  1. Genera URL presignada (1h) de MinIO para `processed-images/{guid}.{ext}`
  2. Actualiza la BD con el resumen final
- **BD — qué guarda:**
  - En `Solicitud`: `Id_Fichero` (URL presignada), `Fin_Solicitud`, `Num_Imagenes_Pixeladas`, Estado → `COMPLETED`
  - Registra el momento que terminó el proceso y el resumen (total de caras y menores)
- **MinIO:** genera URL presignada de `processed-images/{guid}.{ext}`
- **Publica:** nada (fin del pipeline)

---

### 3. Face Detection Service (DC)
- **Consume:** `cmd.face_detection` (T2)
- **Produce:** `evt.face_detection.completed` (T3)
- **Entrada:** imagen cruda en MinIO (`raw-images/{guid}.{ext}`)
- **Salida:** lista de bounding boxes `[{num_cara, x, y, w, h}]`
- **Tecnología:** YOLOv8 (`yolov8n-face.pt`, umbral de confianza 0.4)

### 4. Age Detection Service (DE)
- **Consume:** `cmd.age_detection` (T4) — un mensaje por cara
- **Produce:** `evt.age_detection.completed` (T5) — un mensaje por cara
- **Entrada:** recorte individual de una cara en MinIO (`face-crops/{guid}/cara_{num_cara}.{ext}`)
- **Salida:** clasificación binaria menor/adulto (`{guid, num_cara, edad_estimada, es_menor, confianza_modelo}`)
- **Tecnología:** MobileNetV2 custom (`age_classifier.keras`) — clasificador binario entrenado con transfer learning
- **Modelo:** `age-detection/model/age_classifier.keras` (generado por `training/train_age_model.py`)
- **Clasificación:** `prob >= MINOR_PROB_THRESHOLD` (default 0.5) → menor; devuelve `edad_estimada=12` (menor) o `35` (adulto)
- **Nota:** `Edad` en BD es un valor representativo de la clase, no una edad real estimada

### 5. Pixelation Service (Pixel)
- **Consume:** `cmd.pixelation` (T6)
- **Produce:** `evt.pixelation.completed` (T7)
- **Entrada:** imagen original + lista de caras que son menores `[{num_cara, x, y, w, h}]`
- **Salida:** imagen con las caras de menores pixeladas, guardada en `processed-images/{guid}.{ext}`
- **Función:** si la lista de menores está vacía, copia la imagen original a `processed-images` sin modificarla
- **Tecnología:** OpenCV (resize down + resize up con `INTER_NEAREST`, bloque 20px)

### 6. MinIO
- Almacenamiento de objetos
- Bucket `raw-images` — imágenes originales: clave `{guid}.{ext}`
- Bucket `face-crops` — recortes individuales de cada cara: clave `{guid}/cara_{num_cara}.{ext}`
- Bucket `processed-images` — imágenes pixeladas (o copia del original si no hay menores): clave `{guid}.{ext}`
- El GUID es el identificador único de cada solicitud

---

## Topics de Kafka

| Topic | Label | Productor | Consumidor |
|---|---|---|---|
| `images.raw` | T1 | API Gateway | Orquestador 1 |
| `cmd.face_detection` | T2 | Orquestador 1 | Face Detection |
| `evt.face_detection.completed` | T3 | Face Detection | Orquestador 2 |
| `cmd.age_detection` | T4 | Orquestador 2 (1 msg/cara) | Age Detection |
| `evt.age_detection.completed` | T5 | Age Detection (1 msg/cara) | Orquestador 3 |
| `cmd.pixelation` | T6 | Orquestador 3 | Pixelation |
| `evt.pixelation.completed` | T7 | Pixelation | Orquestador 4 |
| `dead.letter.queue` | — | Cualquier servicio en error | — |

> Los topics se crean automáticamente (`KAFKA_AUTO_CREATE_TOPICS_ENABLE: true`). No hay contenedor `kafka-init`.

---

## Flujo conceptual

```
1.  Cliente        → POST /images                  → API Gateway
2.  API Gateway    → sube imagen a MinIO            → raw-images/{guid}.ext
                   → inserta Solicitud (PENDING)
                   → publica                        → T1: images.raw

3.  Orquestador 1  → consume T1
                   → BD: Estado=FACE_DETECTION, Inicio_Deteccion_Caras
                   → publica                        → T2: cmd.face_detection

4.  Face Det.      → descarga raw-images/{guid}.ext
                   → detecta TODAS las caras (bounding boxes)
                   → publica                        → T3: evt.face_detection.completed

5.  Orquestador 2  → BD: Fin_Deteccion_Caras, Num_Imagenes_Total
                   → BD: inserta fila en Imagenes por cada cara (bbox)
                   → descarga raw-images/{guid}.ext
                   → recorta cada cara → sube a face-crops/{guid}/cara_N.ext
                   → publica N mensajes             → T4: cmd.age_detection (uno por cara)
                   (si N=0 → publica directamente   → T6: cmd.pixelation con lista vacía)

6.  Age Det.       → descarga face-crops/{guid}/cara_N.ext
                   → clasifica menor/adulto con MobileNetV2 custom
                   → publica                        → T5: evt.age_detection.completed (uno por cara)

7.  Orquestador 3  → acumula resultados hasta tener todas las caras
                   → BD: actualiza Edad y Es_Menor en Imagenes por cada resultado
                   → BD: Inicio_Edad (primer resultado), Fin_Edad (último resultado)
                   → cuando todos llegaron:
                   → publica                        → T6: cmd.pixelation + lista de menores

8.  Pixelation     → descarga raw-images/{guid}.ext
                   → pixela caras de menores (bloque 20px)
                   → sube a processed-images/{guid}.ext
                   → publica                        → T7: evt.pixelation.completed

9.  Orquestador 4  → genera URL presignada de processed-images/{guid}.ext
                   → BD: Id_Fichero, Fin_Solicitud, Num_Imagenes_Pixeladas, Estado=COMPLETED

10. Cliente        → GET /images/{guid}             → API Gateway devuelve estado y métricas
```

---

## Esquema de base de datos

```sql
CREATE TABLE IF NOT EXISTS Solicitud (
    Id_Solicitud                    SERIAL PRIMARY KEY,
    GUID_Solicitud                  VARCHAR(255),
    URL_Imagen_Original             VARCHAR(255),   -- URL presignada de la imagen original en raw-images
    Id_Fichero                      VARCHAR(255),   -- URL presignada de la imagen procesada en processed-images
    Inicio_Solicitud                TIMESTAMP,
    Fin_Solicitud                   TIMESTAMP,
    Inicio_Deteccion_Caras          TIMESTAMP,
    Fin_Deteccion_Caras             TIMESTAMP,
    Inicio_Edad                     TIMESTAMP,
    Fin_Edad                        TIMESTAMP,
    Inicio_Pixelado                 TIMESTAMP,
    Fin_Pixelado                    TIMESTAMP,
    Inicio_Almacenamiento_Solicitud TIMESTAMP,
    Fin_Almacenamiento_Solicitud    TIMESTAMP,
    Num_Imagenes_Total              INT,            -- total de caras detectadas
    Num_Imagenes_Pixeladas          INT,            -- caras de menores pixeladas
    Estado                          VARCHAR(50)     -- PENDING, FACE_DETECTION, AGE_DETECTION, PIXELATION, COMPLETED, ERROR
);

CREATE TABLE IF NOT EXISTS Imagenes (
    Id_Imagen    SERIAL PRIMARY KEY,
    Id_Solicitud INT,
    Num_Cara     INT,            -- índice de la cara dentro de la imagen (1-based)
    URL_Imagen   VARCHAR(255),   -- URL presignada del recorte en face-crops
    x            INT,            -- bounding box: posición x
    y            INT,            -- bounding box: posición y
    w            INT,            -- bounding box: ancho
    h            INT,            -- bounding box: alto
    Edad         INT,            -- valor representativo de clase: 12 (menor) o 35 (adulto)
    Es_Menor     BOOLEAN,        -- TRUE si edad < 18
    Escore       DECIMAL(5,4),   -- confianza del modelo de estimación de edad
    Estado       VARCHAR(50),
    FOREIGN KEY (Id_Solicitud) REFERENCES Solicitud(Id_Solicitud)
);
```

**Quién escribe qué:**
| Campo | Lo escribe |
|---|---|
| `Solicitud.URL_Imagen_Original`, `Inicio_Solicitud`, `Estado=PENDING` | API Gateway |
| `Solicitud.Inicio_Deteccion_Caras`, `Estado=FACE_DETECTION` | Orquestador 1 |
| `Solicitud.Fin_Deteccion_Caras`, `Num_Imagenes_Total` + filas `Imagenes` (Num_Cara, URL_Imagen, bbox) | Orquestador 2 |
| `Imagenes.Edad`, `Imagenes.Es_Menor`, `Imagenes.Escore` + `Solicitud.Inicio_Edad/Fin_Edad` | Orquestador 3 |
| `Solicitud.Id_Fichero`, `Fin_Solicitud`, `Inicio/Fin_Pixelado`, `Inicio/Fin_Almacenamiento_Solicitud`, `Num_Imagenes_Pixeladas`, `Estado=COMPLETED` | Orquestador 4 |

---

## Estados de una solicitud

```
PENDING → FACE_DETECTION → AGE_DETECTION → PIXELATION → COMPLETED
```

> Si no hay caras detectadas: 2Or publica `cmd.pixelation` con lista vacía → Pixelation copia la imagen original → 4Or completa.

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
│   ├── download_model.bat         ← descarga age_classifier.keras desde GitHub Releases
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
│   │   └── age_classifier.keras   ← generado por training/
│   └── requirements.txt
├── pixelation/
│   ├── Dockerfile
│   ├── main.py
│   └── requirements.txt
├── training/
│   ├── Dockerfile
│   ├── train_age_model.py         ← entrena el modelo MobileNetV2
│   └── requirements.txt
├── reports/
│   ├── dataset_report.py
│   ├── dataset_analysis.txt
│   └── dataset_analysis.json
└── db/
    └── init.sql
```

---

## Notas de implementación

- Las imágenes **no viajan dentro de los mensajes Kafka**. Se guardan en MinIO y los eventos transportan solo la referencia (bucket + clave).
- El **GUID** es el identificador único de cada solicitud. Se usa como clave en los tres buckets de MinIO.
- Age Detection procesa **una cara a la vez**. Orquestador 2 envía N mensajes a T4, uno por cara recortada.
- El modelo de clasificación de edad es un **MobileNetV2** entrenado con transfer learning en dos fases (cabeza → fine-tuning). Se entrena con `training/train_age_model.py` y se guarda en `age-detection/model/age_classifier.keras`. El campo `Edad` en BD es un valor representativo (12 = menor, 35 = adulto), no una edad real.
- El modelo puede obtenerse de dos formas: entrenándolo localmente (`training/`) o descargándolo desde GitHub Releases con `scripts/download_model.bat`. El contenedor `age-detection` no arranca sin él.
- Orquestador 3 implementa el **patrón de agregación**: acumula resultados de T5 hasta recibir `Num_Imagenes_Total` respuestas antes de publicar en T6.
- El API Gateway crea los tres buckets de MinIO al arrancar si no existen.
- Kafka usa modo **KRaft** (sin Zookeeper). Los topics se crean automáticamente.
- Cada servicio Python usa `kafka-python` como cliente Kafka y `psycopg2-binary` para PostgreSQL.
- Los orquestadores son los únicos servicios que escriben en BD.
- No existe `storage-service` como contenedor independiente — Orquestador 4 genera la URL presignada directamente.

---

## Gestión de errores

- **Dead-letter queue:** mensajes fallidos se publican en `dead.letter.queue`
- **Idempotencia:** cada servicio es idempotente (mismo GUID = mismo resultado)
- **Reintentos de conexión:** cada servicio reintenta conectar a Kafka hasta 15 veces con espera de 5s
