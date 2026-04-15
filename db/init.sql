-- ─────────────────────────────────────────────
-- Schema inicial del proyecto Pixelado de Menores
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS Solicitud (
    Id_Solicitud                    SERIAL PRIMARY KEY,
    GUID_Solicitud                  VARCHAR(255) UNIQUE NOT NULL,
    Id_Fichero                      VARCHAR(255),
    Inicio_Solicitud                TIMESTAMP,
    Fin_Solicitud                   TIMESTAMP,
    Inicio_Deteccion_Caras          TIMESTAMP,
    Fin_Deteccion_Caras             TIMESTAMP,
    Inicio_Almacenamiento_Solicitud TIMESTAMP,
    Fin_Almacenamiento_Solicitud    TIMESTAMP,
    Num_Imagenes_Total              INT DEFAULT 0,
    Num_Imagenes_Pixeladas          INT DEFAULT 0,
    -- PENDING | FACE_DETECTION | AGE_DETECTION | PIXELATION | STORAGE | COMPLETED | ERROR
    Estado                          VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    Resultado_URL                   VARCHAR(500),
    Intentos                        INT DEFAULT 0,
    Creado_En                       TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS Imagenes (
    Id_Imagen       SERIAL PRIMARY KEY,
    Id_Solicitud    INT NOT NULL REFERENCES Solicitud(Id_Solicitud),
    Face_Id         INT,
    Bbox_X          INT,
    Bbox_Y          INT,
    Bbox_W          INT,
    Bbox_H          INT,
    Edad_Estimada   INT,
    Es_Menor        BOOLEAN,
    Inicio_Edad     TIMESTAMP,
    Fin_edad        TIMESTAMP,
    Inicio_Pixelado TIMESTAMP,
    Fin_Pixelado    TIMESTAMP,
    -- PENDING | AGE_DETECTED | PIXELATED | SKIPPED
    Estado          VARCHAR(50) NOT NULL DEFAULT 'PENDING'
);

CREATE INDEX IF NOT EXISTS idx_solicitud_guid  ON Solicitud(GUID_Solicitud);
CREATE INDEX IF NOT EXISTS idx_solicitud_estado ON Solicitud(Estado);
CREATE INDEX IF NOT EXISTS idx_imagenes_solicitud ON Imagenes(Id_Solicitud);
