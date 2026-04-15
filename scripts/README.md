# Scripts de gestión del proyecto

Ejecuta todos los scripts desde la **raíz del proyecto**, no desde dentro de la carpeta `scripts/`.

```cmd
scripts\nombre-del-script.bat
```

---

## Arranque por fases

Sigue este orden la primera vez que levantes el sistema.

### `fase1-infra-up.bat`
Levanta toda la infraestructura base: Kafka, PostgreSQL y MinIO.
También ejecuta los contenedores de inicialización que crean los topics de Kafka y los buckets de MinIO.
Ejecuta este script antes que cualquier otro.

```cmd
scripts\fase1-infra-up.bat
```

---

### `fase2-api-gateway-up.bat`
Construye y arranca el servicio **API Gateway**.
Tras arrancar muestra los logs en tiempo real (Ctrl+C para salir sin parar el contenedor).
Requiere que la Fase 1 esté levantada.

```cmd
scripts\fase2-api-gateway-up.bat
```

---

### `fase3-orchestrator-up.bat`
Construye y arranca los **4 Orquestadores**:
- **orchestrator-1**: escucha `images.raw` → lanza detección de caras
- **orchestrator-2**: escucha `evt.face_detection.completed` → lanza detección de edad
- **orchestrator-3**: escucha `evt.age_detection.completed` → lanza pixelado o almacenamiento
- **orchestrator-4**: escucha `evt.pixelation.completed` y `evt.storage.completed` → cierra el pipeline

Requiere que las Fases 1 y 2 estén levantadas.

```cmd
scripts\fase3-orchestrator-up.bat
```

---

### `fase4-face-detection-up.bat`
Construye y arranca el servicio de **detección de caras** (OpenCV Haar Cascades).
Descarga imágenes de MinIO, detecta caras y devuelve sus bounding boxes.

```cmd
scripts\fase4-face-detection-up.bat
```

---

### `fase5-age-detection-up.bat`
Construye y arranca el servicio de **estimación de edad** (DeepFace).
La primera build puede tardar entre 5 y 10 minutos porque descarga los pesos del modelo (~500 MB).
Las siguientes builds son rápidas porque los pesos quedan en caché.

```cmd
scripts\fase5-age-detection-up.bat
```

---

### `fase6-pixelation-up.bat`
Construye y arranca el servicio de **pixelado**.
Recibe los bounding boxes de los menores y aplica el efecto de pixelado sobre la imagen.

```cmd
scripts\fase6-pixelation-up.bat
```

---

### `fase7-storage-up.bat`
Construye y arranca el **Storage Service**.
Guarda la imagen procesada en MinIO, genera la URL de acceso y marca la solicitud como COMPLETED en la base de datos.

```cmd
scripts\fase7-storage-up.bat
```

---

## Utilidades

### `status.bat`
Muestra el estado actual del sistema de un vistazo:
- Estado de todos los contenedores (running / exited)
- Lista de topics creados en Kafka
- Resumen de solicitudes en PostgreSQL agrupadas por estado

```cmd
scripts\status.bat
```

---

### `logs.bat [servicio]`
Muestra los logs en tiempo real. Si no se indica servicio, muestra los de todos.
Pulsa Ctrl+C para dejar de ver los logs sin parar el contenedor.

```cmd
scripts\logs.bat                  # todos los servicios
scripts\logs.bat api-gateway
scripts\logs.bat orchestrator-1
scripts\logs.bat orchestrator-2
scripts\logs.bat orchestrator-3
scripts\logs.bat orchestrator-4
scripts\logs.bat face-detection
scripts\logs.bat age-detection
scripts\logs.bat pixelation
scripts\logs.bat storage-service
```

---

### `test-pipeline.bat <ruta-imagen>`
Envía una imagen al API Gateway y muestra el GUID devuelto.
Usa ese GUID para consultar el resultado cuando el pipeline haya terminado.

```cmd
scripts\test-pipeline.bat C:\Users\Dario\Pictures\foto.jpg
```

Luego consulta el resultado:
```cmd
curl http://localhost:8000/images/{guid_solicitud}
```

---

### `down.bat`
Para todos los contenedores. **Los datos se conservan** (base de datos, imágenes en MinIO, offsets de Kafka).
Usa este script al terminar de trabajar.

```cmd
scripts\down.bat
```

---

### `reset.bat`
Para todos los contenedores y **borra todos los datos** (volúmenes de PostgreSQL, MinIO y Kafka).
Pide confirmación antes de ejecutar. Útil para empezar desde cero.

```cmd
scripts\reset.bat
```

---

## Flujo de trabajo habitual

```
1. scripts\fase1-infra-up.bat         ← solo la primera vez o tras un reset
2. scripts\fase2-api-gateway-up.bat
3. scripts\fase3-orchestrator-up.bat
4. scripts\fase4-face-detection-up.bat
5. scripts\fase5-age-detection-up.bat
6. scripts\fase6-pixelation-up.bat
7. scripts\fase7-storage-up.bat
8. scripts\status.bat                 ← verifica que todo está en verde
9. scripts\test-pipeline.bat foto.jpg ← prueba el flujo completo
```

Al terminar de trabajar:
```
scripts\down.bat
```
