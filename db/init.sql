CREATE TABLE IF NOT EXISTS Solicitud (
    Id_Solicitud                    SERIAL PRIMARY KEY,
    GUID_Solicitud                  VARCHAR(255),
    Id_Fichero                      VARCHAR(255),
    Inicio_Solicitud                TIMESTAMP,
    Fin_Solicitud                   TIMESTAMP,
    Inicio_Deteccion_Caras          TIMESTAMP,
    Fin_Deteccion_Caras             TIMESTAMP,
    Inicio_Almacenamiento_Solicitud TIMESTAMP,
    Fin_Almacenamiento_Solicitud    TIMESTAMP,
    Num_Imagenes_Total              INT,
    Num_Imagenes_Pixeladas          INT,
    Estado                          VARCHAR(50),
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

CREATE INDEX IF NOT EXISTS idx_solicitud_guid   ON Solicitud(GUID_Solicitud);
CREATE INDEX IF NOT EXISTS idx_solicitud_estado ON Solicitud(Estado);
CREATE INDEX IF NOT EXISTS idx_imagenes_solicitud ON Imagenes(Id_Solicitud);
