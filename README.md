# Pixelado de caras de menores — arquitectura distribuida basada en eventos

Sistema distribuido que recibe una imagen, detecta rostros, estima la edad de cada cara y pixela automáticamente las de personas menores de 18 años.

La solución está montada como **pipeline event-driven con Kafka**, persistencia en **PostgreSQL** y almacenamiento de imágenes en **MinIO**, siguiendo la idea general del enunciado del proyecto.

![Flujo del sistema](assets/flujo.jpeg)

---

## Tecnologías usadas

- **Python 3.11**
- **FastAPI**
- **Kafka**
- **PostgreSQL**
- **MinIO**
- **OpenCV**
- **Ultralytics YOLOv8**
- **DeepFace**
- **Docker / Docker Compose**

---

## Estructura del proyecto

```text
pixelar-menores/
├── age-detection/           # Estimación de edad de cada cara recortada
├── api-gateway/             # API REST para subir imágenes y consultar resultados
├── assets/                  # Recursos del proyecto (diagrama, documentación)
├── db/                      # Script de inicialización de PostgreSQL
├── ejemplos/                # Imágenes de prueba
├── face-detection/          # Detección de caras con YOLOv8
├── orchestrator-1/          # Entrada del pipeline
├── orchestrator-2/          # Recorte de caras y envío a edad
├── orchestrator-3/          # Agregación de edades y decisión de pixelado
├── orchestrator-4/          # Cierre del pipeline y actualización final
├── pixelation/              # Pixelado de caras de menores
├── scripts/                 # Scripts .bat para levantar y probar el sistema
├── storage-service/         # Servicio preparado pero no conectado al flujo actual
├── .env                     # Variables de entorno base
├── docker-compose.yml       # Orquestación completa del sistema
├── INSTRUCCIONES.md         # Guía de desarrollo y pruebas
└── README.md
```

---

## Servicios del sistema

### 1. API Gateway
**Carpeta:** `api-gateway/`

Es la puerta de entrada del sistema.

Hace lo siguiente:
- recibe una imagen por HTTP,
- la guarda en MinIO en el bucket `raw-images`,
- crea una fila en la tabla `Solicitud`,
- publica un evento en Kafka en el topic `images.raw`,
- permite consultar el estado del procesamiento y el resultado final.

### 2. Orchestrator 1
**Carpeta:** `orchestrator-1/`

Escucha `images.raw`.

Se encarga de:
- actualizar la solicitud en base de datos,
- marcar el inicio de la detección de caras,
- publicar `cmd.face_detection`.

### 3. Face Detection Service
**Carpeta:** `face-detection/`

Escucha `cmd.face_detection`.

Se encarga de:
- descargar la imagen original desde MinIO,
- detectar las caras con **YOLOv8**,
- devolver la lista de bounding boxes en `evt.face_detection.completed`.

### 4. Orchestrator 2
**Carpeta:** `orchestrator-2/`

Escucha `evt.face_detection.completed`.

Se encarga de:
- actualizar la solicitud con el número de caras detectadas,
- recortar cada cara de la imagen original,
- guardar esos recortes en MinIO en `face-crops`,
- insertar una fila por cara en la tabla `Imagenes`,
- publicar un `cmd.age_detection` por cada cara.

Si no se detectan caras, envía directamente un `cmd.pixelation` con lista vacía.

### 5. Age Detection Service
**Carpeta:** `age-detection/`

Escucha `cmd.age_detection`.

Se encarga de:
- descargar el recorte de cada cara desde MinIO,
- estimar la edad con **DeepFace**,
- clasificar si la persona es menor o no,
- publicar el resultado en `evt.age_detection.completed`.

### 6. Orchestrator 3
**Carpeta:** `orchestrator-3/`

Escucha `evt.age_detection.completed`.

Se encarga de:
- actualizar la tabla `Imagenes` con edad, score y si es menor,
- esperar a que lleguen todas las caras de una misma solicitud,
- construir la lista de caras menores,
- publicar `cmd.pixelation`.

### 7. Pixelation Service
**Carpeta:** `pixelation/`

Escucha `cmd.pixelation`.

Se encarga de:
- descargar la imagen original,
- pixelar únicamente las caras marcadas como menores,
- guardar la imagen final en MinIO en `processed-images`,
- publicar `evt.pixelation.completed`.

Si no hay menores, copia la imagen original al bucket de procesadas sin modificarla.

### 8. Orchestrator 4
**Carpeta:** `orchestrator-4/`

Escucha `evt.pixelation.completed`.

Se encarga de:
- generar la URL firmada del resultado,
- actualizar la tabla `Solicitud`,
- registrar tiempos finales,
- dejar la solicitud en estado `COMPLETED`.

### 9. Storage Service
**Carpeta:** `storage-service/`

Está preparado para un flujo con `cmd.storage` y `evt.storage.completed`, pero **no está integrado en el `docker-compose` actual**.

---

## Infraestructura

### Kafka
Se usa como bus de eventos para comunicar todos los servicios de forma desacoplada.

### PostgreSQL
Se usa para guardar:
- el estado de cada solicitud,
- tiempos de cada fase,
- número de caras,
- datos individuales de cada rostro.

Tablas principales:
- `Solicitud`
- `Imagenes`

### MinIO
Se usa como almacenamiento compatible con S3.

Buckets usados por el sistema:
- `raw-images` → imagen original
- `face-crops` → recortes de cada cara
- `processed-images` → imagen final procesada

---

## Topics y flujo de eventos

## Topics activos en la versión actual

- `images.raw`
- `cmd.face_detection`
- `evt.face_detection.completed`
- `cmd.age_detection`
- `evt.age_detection.completed`
- `cmd.pixelation`
- `evt.pixelation.completed`
- `dead.letter.queue`

## Topics preparados pero no usados en el flujo actual

- `cmd.storage`
- `evt.storage.completed`

## Flujo real implementado

```text
Cliente
  ↓ HTTP POST /images
API Gateway
  ↓ images.raw
Orchestrator 1
  ↓ cmd.face_detection
Face Detection
  ↓ evt.face_detection.completed
Orchestrator 2
  ↓ cmd.age_detection (uno por cara)
Age Detection
  ↓ evt.age_detection.completed
Orchestrator 3
  ↓ cmd.pixelation
Pixelation
  ↓ evt.pixelation.completed
Orchestrator 4
  ↓
Solicitud COMPLETED + URL final en MinIO
```
---

## Proceso de desarrollo (storytelling)

En esta sección se describe cómo se ha ido construyendo el sistema paso a paso, incluyendo decisiones, problemas encontrados y cómo se han resuelto.

---

### 1. Punto de partida: entender el problema

El proyecto comienza con un objetivo claro:

> Crear un sistema distribuido capaz de detectar caras, estimar la edad y pixelar automáticamente a menores.

Desde el principio se decidió usar una **arquitectura event-driven con Kafka**, en lugar de un enfoque clásico con APIs síncronas, para trabajar con microservicios desacoplados.

---

### 2. Montaje de la infraestructura base

Lo primero que se implementó fue la base del sistema:

- Kafka como bus de eventos
- PostgreSQL para persistencia
- MinIO como almacenamiento de imágenes

Aquí surgieron los primeros problemas típicos:

- Kafka no arrancaba correctamente (problemas con listeners y puertos)
- Conexiones entre contenedores fallaban por nombres de host
- MinIO no creaba buckets automáticamente

Solución:
- Ajustar correctamente `KAFKA_ADVERTISED_LISTENERS`
- Usar nombres de servicio de Docker en lugar de `localhost`
- Crear buckets desde código si no existen

---

### 3. Primer servicio: API Gateway

Una vez la infraestructura estaba lista, se desarrolló el **API Gateway**.

Funcionalidad inicial:
- Recibir imagen por HTTP
- Guardarla en MinIO
- Registrar solicitud en PostgreSQL
- Publicar evento `images.raw` en Kafka

Problemas encontrados:

- Errores al subir archivos a MinIO
- Fallos de serialización al enviar eventos a Kafka

 Solución:
- Ajustar cliente S3 correctamente
- Estandarizar eventos en JSON

---

### 4. Introducción del flujo con orquestadores

En lugar de conectar servicios directamente, se optó por un diseño con **múltiples orquestadores**.

Esto permitió:
- Separar responsabilidades
- Controlar mejor el estado del pipeline
- Facilitar debugging

Se dividió el flujo en:

- Orchestrator 1 → inicio
- Orchestrator 2 → recorte de caras
- Orchestrator 3 → agregación de edades
- Orchestrator 4 → cierre del proceso

Problema clave:

- Desincronización entre eventos (llegaban antes de tiempo o incompletos)

 Solución:
- Controlar estado en base de datos
- Esperar a que lleguen todos los eventos necesarios antes de avanzar

---

### 5. Detección de caras (Face Detection)

Se implementó el servicio de detección de caras usando **YOLOv8**.

Función:
- Leer imagen desde MinIO
- Detectar caras
- Enviar bounding boxes

Problemas:

-  Modelos pesados (tiempo de carga alto)
-  Rutas de archivos dentro de Docker

Solución:
- Inicializar modelo una sola vez
- Manejar correctamente paths dentro del contenedor

---

### 6. Recorte de caras y envío a Age Detection

El **Orchestrator 2** se convirtió en una pieza clave:

- Recorta caras detectadas
- Las guarda en MinIO
- Genera un evento por cada cara

Problema importante:

- Gestión de múltiples caras por imagen

Solución:
- Crear una fila por cara en BD
- Procesar cada cara de forma independiente

---

### 7. Estimación de edad (Age Detection)

Se implementó usando **DeepFace**.

Función:
- Leer cada cara recortada
- Estimar edad
- Clasificar menor/adulto

Problemas:

- DeepFace es lento y pesado
- Resultados inconsistentes en algunas imágenes

Solución:
- Aceptar latencia como trade-off
- Simplificar lógica de clasificación (<18)

---

### 8. Agregación de resultados

El **Orchestrator 3**:

- Espera todas las caras
- Decide si hay menores
- Lanza pixelado

Problema clave:

- Saber cuándo han llegado todas las caras

Solución:
- Usar contador en BD (`Num_Imagenes_Total`)
- Comparar con eventos recibidos

---

### 9. Pixelado de caras

Servicio con OpenCV:

- Pixelar solo caras de menores
- Mantener intactas las demás

Problemas:

- Coordenadas incorrectas
- Pixelado fuera de la zona

Solución:
- Ajustar bounding boxes correctamente
- Validar límites de imagen

---

### 10. Cierre del pipeline

El **Orchestrator 4**:

- Genera URL final
- Marca estado como `COMPLETED`
- Guarda métricas

Aquí se consolidó todo el flujo end-to-end.

---

### 11. Problemas globales del sistema

Durante el desarrollo aparecieron problemas típicos de sistemas distribuidos:

-  Mensajes duplicados
-  Eventos fuera de orden
-  Servicios arrancando antes que Kafka

 Soluciones aplicadas:

- Reintentos implícitos mediante consumo continuo
- Uso de base de datos como fuente de verdad
- Esperas y retries en conexiones

### 12. Estado actual

Actualmente el sistema:

- ✔ Procesa imágenes de extremo a extremo
- ✔ Detecta caras
- ✔ Estima edad
- ✔ Pixela menores
- ✔ Guarda resultados en MinIO
- ✔ Permite consulta desde API

Y todo esto funcionando con arquitectura distribuida basada en eventos

### 13. Próximos pasos

- Integrar `storage-service` en el flujo real
- Añadir dead-letter queue completa
- Mejorar rendimiento de modelos
- Añadir métricas (latencia, throughput)
- Tests end-to-end automatizados

---

## Cómo ejecutar el sistema con Docker Compose

## Requisitos previos

- Docker Desktop instalado
- Docker Compose disponible
- Puerto `8000` libre para la API
- Puerto `9000` libre para MinIO API
- Puerto `9001` libre para consola de MinIO
- Puerto `5432` libre para PostgreSQL
- Puerto `9092` libre para Kafka

---

## Cómo ejecutar el sistema por fases

En `scripts/` hay archivos `.bat` para Windows.

Ejecuta siempre los scripts desde la **raíz del proyecto**.

### Orden recomendado

```cmd
scripts\fase1-infra-up.bat
scripts\fase2-api-gateway-up.bat
scripts\fase3-orchestrator-up.bat
scripts\fase4-face-detection-up.bat
scripts\fase5-age-detection-up.bat
scripts\fase6-pixelation-up.bat
scripts\status.bat
```

### Otros scripts útiles

```cmd
scripts\logs.bat
scripts\test-pipeline.bat C:\ruta\a\tu\imagen.jpg
scripts\down.bat
scripts\reset.bat
```

### Nota sobre los scripts

Los scripts y su `scripts/README.md` sirven como apoyo para las pruebas, pero parte de esa documentación interna refleja una versión anterior del flujo. La referencia más fiel al estado actual del proyecto es el código de `docker-compose.yml` y los `main.py` de cada servicio.

---

## Cómo probar el sistema con cualquier imagen

Puedes usar:
- una imagen tuya,
- una imagen descargada,
- o cualquiera de las que ya vienen en `ejemplos/`.

Por ejemplo:
- `ejemplos/pexels-bertellifotografia-15485500.jpg`
- `ejemplos/pexels-bertellifotografia-34520968.jpg`
- `ejemplos/pexels-israwmx-17030111.jpg`

## Subir una imagen al sistema

### Opción 1: usando `curl`

```bash
curl -X POST http://localhost:8000/images -F "file=@ejemplos/pexels-israwmx-17030111.jpg"
```

Respuesta esperada:

```json
{
  "guid_solicitud": "...",
  "id_solicitud": 1,
  "estado": "PENDING"
}
```

Guarda el `guid_solicitud`, porque lo necesitarás para consultar el resultado.

### Opción 2: usando una imagen cualquiera de tu PC

#### En Windows (cmd)

```cmd
curl -X POST http://localhost:8000/images -F "file=@C:\Users\TuUsuario\Pictures\foto.jpg"
```

#### En Linux / macOS

```bash
curl -X POST http://localhost:8000/images -F "file=@/ruta/a/tu/foto.jpg"
```

---

## Cómo consultar el resultado

### Consultar una solicitud concreta

```bash
curl http://localhost:8000/images/<guid_solicitud>
```

Cuando termine el pipeline verás algo así:

```json
{
  "estado": "COMPLETED",
  "url_resultado": "http://...",
  "metricas": {
    "num_imagenes_total": 2,
    "num_imagenes_pixeladas": 1
  }
}
```

### Listar solicitudes

```bash
curl "http://localhost:8000/images?limit=10&offset=0"
```

### Filtrar por estado

```bash
curl "http://localhost:8000/images?estado=COMPLETED"
```

### Consultar una cara concreta

```bash
curl http://localhost:8000/images/<guid_solicitud>/cara/1
```

Esto devuelve:
- bounding box,
- edad estimada,
- si es menor,
- score,
- URL del recorte de la cara.

### Healthcheck de la API

```bash
curl http://localhost:8000/health
```

---

## Cómo abrir MinIO

Con el sistema levantado, abre en el navegador:

```text
http://localhost:9001
```

Credenciales por defecto:

```text
usuario: minioadmin
contraseña: minioadmin
```

Dentro de MinIO podrás ver:
- `raw-images` → imagen original subida por la API,
- `face-crops` → recortes de cada cara,
- `processed-images` → imagen final con pixelado.

La API S3 de MinIO queda disponible en:

```text
http://localhost:9000
```

---

## Cómo comprobar Kafka y PostgreSQL

### Ver topics creados en Kafka

```bash
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list
```

### Ver tablas en PostgreSQL

```bash
docker exec postgres psql -U faceuser -d facedb -c "\dt"
```

### Ver solicitudes guardadas

```bash
docker exec postgres psql -U faceuser -d facedb -c "SELECT Id_Solicitud, GUID_Solicitud, Estado, Num_Imagenes_Total, Num_Imagenes_Pixeladas FROM Solicitud;"
```

### Ver caras procesadas

```bash
docker exec postgres psql -U faceuser -d facedb -c "SELECT Id_Imagen, Id_Solicitud, Num_Cara, Edad, Es_Menor, Estado FROM Imagenes;"
```

---

## Cómo ejecutar cada servicio por separado

La forma recomendada es usar **Docker Compose**, pero si quieres probar los servicios de forma más manual puedes hacerlo así.

## 1. Levanta solo la infraestructura

```bash
docker compose up -d kafka postgres minio
```

## 2. Usa estas variables de entorno base

### Linux / macOS

```bash
export KAFKA_BOOTSTRAP_SERVERS=localhost:9092
export POSTGRES_URL=postgresql://faceuser:facepass@localhost:5432/facedb
export MINIO_ENDPOINT=localhost:9000
export MINIO_PUBLIC_ENDPOINT=localhost:9000
export MINIO_ACCESS_KEY=minioadmin
export MINIO_SECRET_KEY=minioadmin
export MINIO_SECURE=false
```

### Windows PowerShell

```powershell
$env:KAFKA_BOOTSTRAP_SERVERS="localhost:9092"
$env:POSTGRES_URL="postgresql://faceuser:facepass@localhost:5432/facedb"
$env:MINIO_ENDPOINT="localhost:9000"
$env:MINIO_PUBLIC_ENDPOINT="localhost:9000"
$env:MINIO_ACCESS_KEY="minioadmin"
$env:MINIO_SECRET_KEY="minioadmin"
$env:MINIO_SECURE="false"
```

## 3. Ejecutar cada `main.py`

### API Gateway

```bash
cd api-gateway
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Orchestrator 1

```bash
cd orchestrator-1
pip install -r requirements.txt
python main.py
```

### Orchestrator 2

```bash
cd orchestrator-2
pip install -r requirements.txt
python main.py
```

### Orchestrator 3

```bash
cd orchestrator-3
pip install -r requirements.txt
python main.py
```

### Orchestrator 4

```bash
cd orchestrator-4
pip install -r requirements.txt
python main.py
```

### Face Detection

```bash
cd face-detection
pip install -r requirements.txt
python main.py
```

### Age Detection

```bash
cd age-detection
pip install -r requirements.txt
python main.py
```

### Pixelation

```bash
cd pixelation
pip install -r requirements.txt
python main.py
```

### Storage Service (solo si más adelante lo conectas al flujo)

```bash
cd storage-service
pip install -r requirements.txt
python main.py
```

---

## Endpoints disponibles

### `POST /images`
Sube una imagen y arranca el pipeline.

### `GET /images`
Lista solicitudes almacenadas.

Parámetros opcionales:
- `limit`
- `offset`
- `estado`

### `GET /images/{guid}`
Devuelve el estado de una solicitud concreta.

### `GET /images/{guid}/cara/{num_cara}`
Devuelve información de una cara concreta.

### `GET /health`
Healthcheck simple del API Gateway.

---

## Qué guarda el sistema

### En MinIO
- imagen original,
- recortes individuales de caras,
- imagen final procesada.

### En PostgreSQL
- estado global de la solicitud,
- tiempos de inicio y fin por fase,
- total de caras,
- caras pixeladas,
- edad estimada por cara,
- clasificación menor/adulto,
- score del modelo.

---

## Prueba rápida recomendada

```bash
docker compose up -d --build
curl -X POST http://localhost:8000/images -F "file=@ejemplos/pexels-israwmx-17030111.jpg"
curl http://localhost:8000/images/<guid_solicitud>
```

Después puedes:
- abrir MinIO en `http://localhost:9001`,
- mirar los logs con `docker compose logs -f`,
- comprobar la BD y los topics.

---

## Mejoras pendientes / observaciones

- Integrar de forma real `storage-service` si se quiere separar completamente el cierre del pipeline.
- Añadir pruebas end-to-end automáticas.
- Añadir manejo más avanzado de errores y reintentos.
- Documentar contratos JSON de eventos.
- Añadir métricas de rendimiento más detalladas.

---

## Autoría

Repositorio del proyecto:

```text
https://github.com/dariolopez05/pixelar-menores
```

