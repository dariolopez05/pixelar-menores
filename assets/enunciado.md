# Proyecto: Identificación y Pixelado de Rostros en Imágenes con Arquitectura Event-Driven

## 1. Definición del proyecto

El proyecto consistirá en el desarrollo de un sistema distribuido basado en eventos (Event-Driven Architecture) que procese imágenes para:

- Detectar rostros en imágenes
- Estimar la edad de cada rostro
- Pixelar automáticamente los rostros de personas menores de 18 años

A diferencia del enfoque tradicional basado en llamadas HTTP síncronas, este sistema utilizará un bus de eventos Kafka para la comunicación entre servicios.

---

## 2. Requisitos Técnicos

### Arquitectura basada en eventos

Todos los servicios deben comunicarse mediante un sistema de mensajería:
- Kafka
- Comunicación asíncrona mediante topics
- Patrón publish/subscribe

### Contenedores Docker

**1. API Gateway (Productor de eventos)**
- Función: Recibir imágenes (HTTP REST), publicarlas en un topic (ej: images.raw)
- Tecnología: FastAPI o Flask

**2. Orquestador (Consumidor + Productor)**
- Función: Coordinar el flujo mediante eventos, enviar imágenes a los siguientes topics según estado
- Alternativa: flujo completamente desacoplado sin orquestador

**3. Face Detection Service (Consumidor + Productor)**
- Consume: images.raw
- Produce: images.faces_detected
- Función: Detectar rostros, generar bounding boxes
- Tecnología: OpenCV, Dlib, YOLO

**4. Age Detection Service (Consumidor + Productor)**
- Consume: images.faces_detected
- Produce: images.age_estimated
- Función: Estimar edad de cada rostro, clasificar: <18 o >=18
- Tecnología: TensorFlow / PyTorch (ResNet o similar)

**5. Pixelation Service (Consumidor + Productor)**
- Consume: images.age_estimated
- Produce: images.processed
- Función: Pixelar rostros de menores
- Tecnología: OpenCV

**6. API Gateway (Consulta a BD y a S3)**
- Función: Consulta a la BD si la imagen ya fue procesada, recoge la imagen del S3
- Tecnología: FastAPI o Flask

**7. minIO (Servicio de Almacenamiento)**
- Función: Almacenamiento compatible con AWS S3

**8. Postgres (Servicio de BD)**
- Función: Almacenamiento de las tablas con la información de las imágenes

### Topics recomendados

```
images.raw.send
images.faces_detected
images.faces_detected.send
images.age_estimated
images.age_estimated.send
images.processed
```

### Docker Compose

Debe incluir: todos los servicios, Kafka (con Zookeeper si es necesario), redes internas privadas.

### Métricas de rendimiento

Cada servicio debe medir:
- Tiempo de procesamiento por evento
- Latencia end-to-end
- Throughput (opcional)

---

## 3. Dataset

Dataset base: https://www.kaggle.com/datasets/frabbisw/facial-age

Se puede proponer alternativa, pero todos los equipos deben usar la misma.

---

## 4. Orquestación

El sistema debe ser completamente desacoplado, sin necesidad de un orquestador central. Sin embargo, vamos a implementar un servicio de Orquestador para coordinar el flujo.

El orquestador actúa como el coordinador del pipeline distribuido. Su responsabilidad no es procesar imágenes, sino supervisar el estado de cada trabajo y decidir cuál es el siguiente servicio que debe intervenir. A partir de los eventos emitidos por los distintos microservicios, actualiza el estado de la imagen, resuelve bifurcaciones del flujo, gestiona errores y activa reintentos o rutas alternativas cuando es necesario.

### 4.1. Flujo Conceptual

```
Cliente -> API Gateway -> images.raw
Orquestador_1 (O1) -> consume images.raw -> guarda estado inicial -> publica cmd.face_detection
Face Detection -> consume cmd.face_detection -> publica evt.face_detection.completed -> guarda las imágenes detectadas
Orquestador_2 (O2) -> decide siguiente paso -> publica cmd.age_detection o cmd.storage
Age Detection -> publica evt.age_detection.completed
Orquestador_3 (O3) -> si hay menores -> cmd.pixelation -> si no -> cmd.storage
Pixelation -> publica evt.pixelation.completed
Orquestador (O4) -> marca COMPLETED
Cliente -> API Gateway (2 endpoints):
  - Petición de Solicitud completa: Recibe metadatos e imagen completa procesada o 404
  - Petición de una cara: Recibe metadatos e imagen de una cara
```

**Observación:** A lo largo de todo el pipeline se arrastra el valor del GUID de la solicitud.

### 4.3. Tablas

```sql
CREATE TABLE Solicitud (
    GUID_Solicitud VARCHAR(255) PRIMARY KEY,
    URL_Imagen_Original VARCHAR(255),
    URL_Imagen_Terminada VARCHAR(255),
    Inicio_Solicitud TIMESTAMP,
    Fin_Solicitud TIMESTAMP,
    Inicio_Deteccion_Caras TIMESTAMP,
    Fin_Deteccion_Caras TIMESTAMP,
    Inicio_Edad TIMESTAMP,
    Fin_edad TIMESTAMP,
    Inicio_Pixelado TIMESTAMP,
    Fin_Pixelado TIMESTAMP,
    Inicio_Almacenamiento_Solicitud TIMESTAMP,
    Fin_Almacenamiento_Solicitud TIMESTAMP,
    Estado VARCHAR(50)
);

CREATE TABLE Imagenes (
    GUID_Solicitud VARCHAR(255),
    Id_Imagen INT,
    URL_Imagen VARCHAR(255),
    Mayor_18 BOOLEAN,
    Escore DECIMAL(5,4),
    Imagen_X INT,
    Imagen_Y INT,
    Imagen_Ancho INT,
    Imagen_Alto INT,
    PRIMARY KEY (GUID_Solicitud, Id_Imagen),
    FOREIGN KEY (GUID_Solicitud) REFERENCES Solicitud(GUID_Solicitud)
);
```

El `Id_Imagen` será un valor entre 0 y X en cada solicitud.

Estados de la solicitud:
- `CREADA`
- `CARAS_DETECTADAS`
- `EDAD_CALCULADA`
- `COMPLETADA`

---

## 5. Almacenamiento

Para el almacenamiento de las imágenes se utilizará minIO (compatible con Amazon S3).

Consideraciones:
- Dentro del bucket de S3 tiene que haber como mínimo dos carpetas: una para las imágenes originales y otra para las definitivas.
- Se guardan tanto las imágenes de las solicitudes como las imágenes de cada una de las caras.

---

## 6. Entregables

- **Repositorio GitHub** con acceso de lectura para el profesor:
  - `README.md`: cómo ejecutar, estructura del proyecto, descripción de cada servicio, topics y flujo de eventos
  - Documentación funcional: diagrama de arquitectura, flujo de eventos, explicación del pipeline, gestión de errores
- **Presentación** (8–10 diapositivas): explicación del proyecto, conclusiones, propuestas de mejora

---

## 7. Desarrollo del Proyecto

- **Planificación:** división por servicios, definición de contratos de eventos (JSON schema)
- **Implementación:** desarrollo independiente por servicio, uso de eventos para integración
- **Pruebas:** unitarias por servicio, integración con Kafka, validación del flujo completo

---

## 8. Evaluación

| Criterio | Descripción |
|---|---|
| Funcionalidad | ¿Se procesan correctamente las imágenes? ¿Se detectan y pixelan menores? |
| Arquitectura | Uso correcto del patrón event-driven, desacoplamiento real, uso adecuado de topics |
| Rendimiento | Latencia, capacidad de procesar múltiples imágenes |
| Calidad del código | Buenas prácticas, estructura clara, manejo de errores |
