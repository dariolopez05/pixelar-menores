# Plan de desarrollo — Pixelado de Menores (Event-Driven)

## Estado actual
- [x] Infraestructura base levantada (Kafka, PostgreSQL, MinIO)
- [x] API Gateway
- [x] Orquestadores (x4)
- [x] Face Detection Service (1 contenedor)
- [x] Age Detection Service (1 contenedor)
- [x] Pixelation Service
- [x] Storage Service
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
- **Hace:** actualiza `Solicitud` (`Fin_Deteccion_Caras`, `Num_Imagenes_Total`), inserta filas en `Imagenes`
- **Publica:** `cmd.age_detection` (si hay caras) o `cmd.storage` (si no hay caras)

### Orquestador 3 — Tras detección de edad
- **Escucha:** `evt.age_detection.completed`
- **Hace:** actualiza `Solicitud` (`Inicio_Edad`, `Fin_Edad`)
- **Publica:** `cmd.pixelation` (si hay menores) o `cmd.storage` (si no hay menores)

### Orquestador 4 — Cierre del pipeline
- **Escucha:** `evt.pixelation.completed` y `evt.storage.completed`
- **Hace (pixelation):** actualiza `Solicitud` (`Inicio_Pixelado`, `Fin_Pixelado`, `Num_Imagenes_Pixeladas`) → publica `cmd.storage`
- **Hace (storage):** marca `Solicitud` como `COMPLETED` con timestamps finales

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
- Usa **OpenCV Haar Cascades** (`haarcascade_frontalface_default.xml`) para detectar todas las caras
- Publica `evt.face_detection.completed` con la lista completa de bounding boxes `[{num_cara, x, y, w, h}]`

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
└── model/   ← pesos de DeepFace se descargan aquí
```

### Paso 5.2 — Implementación
- **1 solo contenedor**
- Consume `cmd.age_detection`
- Descarga imagen de MinIO, recorta cada cara con margen del 10%
- Pasa cada recorte por **DeepFace** (`actions=['age']`)
- Clasifica como menor si `edad < 18`
- Publica `evt.age_detection.completed` con `{edad_estimada, es_menor, confianza_modelo}` por cara

> La primera build descarga los pesos del modelo (~500 MB). Puede tardar varios minutos.

### Paso 5.3 — Levantar y probar
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

## FASE 7 — Storage Service ✅

### Paso 7.1 — Estructura
```
storage-service/
├── Dockerfile
├── requirements.txt
└── main.py
```

### Paso 7.2 — Implementación
- Consume `cmd.storage`
- Genera URL presignada (1 hora de validez) del objeto en MinIO
- Actualiza `Solicitud.Id_Fichero` en PostgreSQL con la URL
- Publica `evt.storage.completed`

### Paso 7.3 — Levantar y probar
```bash
docker compose up -d --build storage-service
```

---

## FASE 8 — Prueba del flujo completo

### Paso 8.1 — Descargar el dataset
Dataset oficial: https://www.kaggle.com/datasets/frabbisw/facial-age

1. Descarga el dataset de Kaggle (requiere cuenta gratuita)
2. Extrae las imágenes en una carpeta local `test-images/` (no se sube al repo)
3. Las imágenes tienen la edad en el nombre del fichero, útil para verificar que el modelo acierta

> El dataset no va en el repositorio. Solo se usa localmente para las pruebas.

### Paso 8.2 — Levantar todo
```bash
docker compose up -d
```

### Paso 8.3 — Enviar una imagen con menores
```bash
curl -X POST http://localhost:8000/images -F "file=@foto_con_menor.jpg"
# Guarda el guid devuelto
```

### Paso 8.4 — Seguir el pipeline en los logs
```bash
docker compose logs -f
```
Debes ver el flujo: `images.raw` → `cmd.face_detection` → `evt.face_detection.completed` → ... → `COMPLETED`

### Paso 8.5 — Recoger el resultado
```bash
curl http://localhost:8000/images/{guid}
# Debe devolver estado COMPLETED
```

### Paso 8.6 — Verificar imagen en MinIO
Abre **http://localhost:9001** → bucket `processed-images` → objeto `{guid}.{ext}` → comprueba que los menores están pixelados.

---

## FASE 9 — Entregables

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

# Ver logs de un servicio concreto
docker compose logs -f orchestrator-1
docker compose logs -f face-detection
docker compose logs -f age-detection

# Reconstruir y reiniciar un servicio tras modificarlo
docker compose up -d --build face-detection

# Parar todo
docker compose down

# Parar y borrar volúmenes (reset completo de BD y MinIO)
docker compose down -v

# Ver mensajes en un topic de Kafka
docker exec kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic images.raw \
  --from-beginning
```
