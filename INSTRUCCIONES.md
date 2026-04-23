# Plan de desarrollo — Pixelado de Menores (Event-Driven)

## Estado actual
- [x] Infraestructura base levantada (Kafka, PostgreSQL, MinIO)
- [x] API Gateway
- [x] Orquestadores (x4)
- [x] Face Detection Service — YOLOv8 (`yolov8n-face.pt` incluido en imagen Docker)
- [x] Age Detection Service — MobileNetV2 custom (`age_classifier.keras`), procesa una cara a la vez
- [x] Pixelation Service
- [ ] Pruebas end-to-end
- [ ] Entregables

---

## FASE 1 — Infraestructura base ✅

### Paso 1.1 — Levantar infraestructura
```bash
docker compose up -d kafka postgres minio
```
Verifica que los 3 contenedores estén en verde:
```bash
docker compose ps
```
Deberías ver `kafka`, `postgres`, `minio` todos como `running`.

> Los topics de Kafka se crean automáticamente (`KAFKA_AUTO_CREATE_TOPICS_ENABLE: true`).
> Los buckets de MinIO (`raw-images`, `processed-images`) los crea el API Gateway al arrancar.

### Paso 1.2 — Verificar Kafka
```bash
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list
```
Los topics aparecen conforme los servicios los usan por primera vez.

### Paso 1.3 — Verificar PostgreSQL (tablas creadas)
```bash
docker exec postgres psql -U faceuser -d facedb -c "\dt"
```
Debes ver las tablas `solicitud` e `imagenes`.

### Paso 1.4 — Verificar MinIO
Abre **http://localhost:9001** → usuario `minioadmin` / contraseña `minioadmin`.
Los buckets `raw-images` y `processed-images` aparecen al levantar el API Gateway.

---

## FASE 2 — API Gateway ✅

### Paso 2.1 — Estructura
```
api-gateway/
├── Dockerfile
├── requirements.txt
└── main.py
```

### Paso 2.2 — Endpoints implementados
- `POST /images` → recibe imagen, la sube a MinIO (`raw-images/{guid}.{ext}`), inserta fila en `Solicitud`, publica en `images.raw`, devuelve `{"guid_solicitud": "...", "estado": "PENDING"}`
- `GET /images/{guid}` → consulta tabla `Solicitud`, devuelve estado y métricas si está `COMPLETED`, o 404 si no existe
- `GET /health` → devuelve `{"status": "ok"}`

> El GUID es el identificador único de la imagen en todo el sistema. La clave del objeto en MinIO es `{guid}.{ext}`.

### Paso 2.3 — Levantar y probar
```bash
docker compose up -d --build api-gateway
```
Prueba:
```bash
curl -X POST http://localhost:8000/images -F "file=@foto.jpg"
curl http://localhost:8000/images/{guid}
```

---

## FASE 3 — Orquestadores (x4) ✅

Hay 4 orquestadores especializados. Ninguno procesa imágenes; solo gestionan el flujo y el estado en BD.

### Orquestador 1 — Entrada del pipeline
- **Escucha:** `images.raw`
- **Hace:** actualiza `Solicitud` (Estado → `FACE_DETECTION`, `Inicio_Deteccion_Caras`)
- **Publica:** `cmd.face_detection`

### Orquestador 2 — Tras detección de caras
- **Escucha:** `evt.face_detection.completed`
- **Hace:** actualiza `Solicitud` (`Fin_Deteccion_Caras`, `Num_Imagenes_Total`), inserta filas en `Imagenes` con bbox
- Descarga imagen cruda de MinIO, recorta cada cara (margen 10%), sube cada recorte a `face-crops/{guid}/cara_N.{ext}`
- **Publica:** N mensajes `cmd.age_detection` (uno por cara); si 0 caras → `cmd.pixelation` con lista vacía

### Orquestador 3 — Agregador de edades
- **Escucha:** `evt.age_detection.completed` (un mensaje por cara)
- **Hace:** acumula resultados en memoria; actualiza `Imagenes` (Edad, Es_Menor, Escore); actualiza `Solicitud` (Inicio_Edad al primero, Fin_Edad al último)
- Cuando llegan todos los resultados (N == Num_Imagenes_Total): publica `cmd.pixelation`
- **Publica:** `cmd.pixelation` con lista de caras menores (puede ser vacía)

### Orquestador 4 — Cierre del pipeline
- **Escucha:** `evt.pixelation.completed`
- **Hace:** genera URL presignada (1h) de `processed-images/{guid}.{ext}` usando MinIO
- Actualiza `Solicitud`: `Id_Fichero` (URL), `Fin_Solicitud`, `Inicio/Fin_Pixelado`, `Inicio/Fin_Almacenamiento_Solicitud`, `Num_Imagenes_Pixeladas`, Estado → `COMPLETED`

### Paso 3.1 — Levantar y probar
```bash
docker compose up -d --build orchestrator-1 orchestrator-2 orchestrator-3 orchestrator-4
```
```bash
docker compose logs -f orchestrator-1 orchestrator-2 orchestrator-3 orchestrator-4
```

---

## FASE 4 — Face Detection Service ✅

### Paso 4.1 — Estructura
```
face-detection/
├── Dockerfile
├── requirements.txt
└── main.py
```

### Paso 4.2 — Implementación
- **1 solo contenedor** que procesa todas las caras de cada imagen
- Consume `cmd.face_detection`
- Descarga imagen de MinIO (`raw-images/{guid}.{ext}`)
- Usa **YOLOv8** (`yolov8n-face.pt`, modelo incluido en la imagen Docker) para detectar caras frontales y de perfil
- Publica `evt.face_detection.completed` con la lista completa de bounding boxes `[{num_cara, x, y, w, h, confianza}]`

> El modelo `yolov8n-face.pt` debe existir en `face-detection/` antes del `docker compose build`. Se descarga de: https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n-face.pt

### Paso 4.3 — Levantar y probar
```bash
docker compose up -d --build face-detection
```

---

## FASE 5 — Age Detection Service ✅

### Paso 5.1 — Estructura
```
age-detection/
├── Dockerfile
├── requirements.txt
├── main.py
└── model/
    └── age_classifier.keras   ← obligatorio, no incluido en el repo
training/
├── Dockerfile
├── requirements.txt
└── train_age_model.py
```

### Paso 5.2 — Implementación
- **1 solo contenedor**, procesa **una cara a la vez** (mensaje por cara)
- Consume `cmd.age_detection` (N mensajes por imagen, uno por cara recortada)
- Descarga el recorte de MinIO (`face-crops/{guid}/cara_N.{ext}`)
- Clasifica con **MobileNetV2 custom** (`age_classifier.keras`): `prob >= 0.5` → menor
- Devuelve `edad_estimada=12` (menor) o `35` (adulto) — valor representativo, no edad real
- Publica `evt.age_detection.completed` con `{guid, num_cara, id_imagen, edad_estimada, es_menor, confianza_modelo}` por cara

### Paso 5.3 — Obtener el modelo (obligatorio antes de levantar el servicio)

**Opción A — Descargar desde GitHub Releases** (recomendado si ya está publicado):
```bat
scripts\download_model.bat
```

**Opción B — Entrenar localmente** (necesario la primera vez o si cambias el dataset):
```bat
docker build -t age-training ./training
docker run --rm -v "%cd%\dataset:/dataset" -v "%cd%\age-detection\model:/model" age-training
```
Con GPU NVIDIA (mucho más rápido):
```bat
docker run --rm --gpus all -v "%cd%\dataset:/dataset" -v "%cd%\age-detection\model:/model" age-training
```

> El dataset debe estar en `dataset/` con carpetas nombradas por edad (p.ej. `017/`).
> El modelo se guarda automáticamente en `age-detection/model/age_classifier.keras`.

### Paso 5.4 — Levantar el servicio
```bash
docker compose up -d --build age-detection
```

---

## FASE 6 — Pixelation Service ✅

### Paso 6.1 — Estructura
```
pixelation/
├── Dockerfile
├── requirements.txt
└── main.py
```

### Paso 6.2 — Implementación
- Consume `cmd.pixelation`
- Descarga imagen original de `raw-images/{guid}.{ext}`
- Para cada bounding box de menor: aplica pixelado (resize down + resize up con `INTER_NEAREST`, bloque de 20px)
- Sube imagen modificada a `processed-images/{guid}.{ext}` (mismo nombre de objeto, distinto bucket)
- Publica `evt.pixelation.completed`

### Paso 6.3 — Levantar y probar
```bash
docker compose up -d --build pixelation
```

---

---

## FASE 7 — Prueba del flujo completo

### Paso 7.1 — Levantar todo
```bash
docker compose up -d --build
```
Verifica que los 11 contenedores están en verde:
```bash
docker compose ps
```

### Paso 7.2 — Enviar una imagen
Coloca una imagen en la carpeta `ejemplos/` (créala si no existe) y ejecuta:
```bash
curl -X POST http://localhost:8000/images -F "file=@ejemplos/foto.jpg" -s | python -m json.tool
```
Guarda el `guid_solicitud` que devuelve, p.ej.:
```json
{
  "guid_solicitud": "abc123...",
  "id_solicitud": 1,
  "estado": "PENDING"
}
```

### Paso 7.3 — Seguir el pipeline en tiempo real
```bash
docker compose logs -f orchestrator-1 orchestrator-2 orchestrator-3 orchestrator-4 face-detection age-detection pixelation
```
Debes ver el flujo: `images.raw` → `cmd.face_detection` → `evt.face_detection.completed` → `cmd.age_detection` → `evt.age_detection.completed` → `cmd.pixelation` → `evt.pixelation.completed` → `COMPLETED`

### Paso 7.4 — Consultar el resultado
```bash
curl -s http://localhost:8000/images/{guid_solicitud} | python -m json.tool
```
Cuando el estado sea `COMPLETED`, `url_resultado` contiene la URL presignada de la imagen procesada.

### Paso 7.5 — Consultar una cara individual
```bash
curl -s http://localhost:8000/images/{guid_solicitud}/cara/1 | python -m json.tool
```
Devuelve bbox, edad estimada, `es_menor` y URL presignada del recorte en `face-crops`.

### Paso 7.6 — Listar todas las solicitudes
```bash
curl -s "http://localhost:8000/images?limit=10&offset=0" | python -m json.tool
# Filtrar por estado:
curl -s "http://localhost:8000/images?estado=COMPLETED" | python -m json.tool
```

### Paso 7.7 — Verificar imagen en MinIO
Abre **http://localhost:9001** (usuario `minioadmin` / contraseña `minioadmin`):
- Bucket `raw-images` → imagen original
- Bucket `face-crops` → recortes individuales por cara
- Bucket `processed-images` → imagen con menores pixelados

### Paso 7.8 — Verificar base de datos
```bash
docker compose exec postgres psql -U faceuser -d facedb -c "SELECT Id_Solicitud, GUID_Solicitud, Estado, Num_Imagenes_Total, Num_Imagenes_Pixeladas FROM Solicitud;"
docker compose exec postgres psql -U faceuser -d facedb -c "SELECT Id_Imagen, Num_Cara, Edad, Es_Menor, Escore FROM Imagenes;"
```

### Paso 7.9 — Dataset de prueba
Dataset oficial con edades etiquetadas: https://www.kaggle.com/datasets/frabbisw/facial-age

Las imágenes tienen la edad en el nombre del fichero, útil para verificar que el modelo acierta.

> El dataset no va en el repositorio. Descárgalo localmente en `test-images/`.

---

## FASE 8 — Entregables

### Paso 9.1 — README.md
Crear un `README.md` con:
- Cómo ejecutar el sistema (`docker compose up -d`)
- Estructura del proyecto
- Descripción de cada servicio
- Topics y flujo de eventos

### Paso 9.2 — Documentación funcional
- Diagrama de arquitectura
- Flujo de eventos
- Explicación del pipeline

### Paso 9.3 — Gestión de errores
Documentar:
- Qué ocurre si falla un servicio
- Dead-letter queue (`dead.letter.queue`)
- Reintentos

### Paso 9.4 — Presentación (8–10 diapositivas)
1. Introducción y objetivo
2. Arquitectura general
3. Flujo de eventos (diagrama)
4. Servicios: API Gateway + Orquestadores
5. Servicios: Face Detection + Age Detection
6. Servicios: Pixelation + Storage
7. Kafka: topics y contratos de mensajes
8. Demo en vivo / capturas
9. Conclusiones
10. Propuestas de mejora

### Paso 9.5 — Subir a GitHub
```bash
git init
git add .
git commit -m "feat: sistema completo de pixelado de menores"
git remote add origin https://github.com/tu-usuario/pixelar-menores.git
git push -u origin main
```

---

## Comandos útiles

```bash
# Ver estado de todos los contenedores
docker compose ps

# Ver logs de un servicio concreto (últimas 50 líneas)
docker compose logs --tail=50 face-detection
docker compose logs --tail=50 age-detection
docker compose logs --tail=50 orchestrator-2
docker compose logs --tail=50 orchestrator-3

# Reconstruir y reiniciar un servicio tras modificarlo
docker compose up -d --build face-detection

# Reconstruir todo desde cero
docker compose up -d --build

# Parar todo
docker compose down

# Parar y borrar volúmenes (reset completo de BD y MinIO)
docker compose down -v

# Ver mensajes en un topic de Kafka
docker exec kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic images.raw \
  --from-beginning

# Ver todos los topics creados
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list

# Consultar BD directamente
docker compose exec postgres psql -U faceuser -d facedb -c "SELECT * FROM Solicitud;"
docker compose exec postgres psql -U faceuser -d facedb -c "SELECT * FROM Imagenes;"

# Dead-letter queue — ver mensajes de error
docker exec kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic dead.letter.queue \
  --from-beginning
```
