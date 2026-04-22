CREATE TABLE IF NOT EXISTS Solicitud (
    Id_Solicitud                    SERIAL PRIMARY KEY,
    GUID_Solicitud                  VARCHAR(255),
    URL_Imagen_Original             TEXT,
    Id_Fichero                      TEXT,
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
    Num_Imagenes_Total              INT,
    Num_Imagenes_Pixeladas          INT,
    Estado                          VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS Imagenes (
    Id_Imagen    SERIAL PRIMARY KEY,
    Id_Solicitud INT,
    Num_Cara     INT,
    URL_Imagen   TEXT,
    x            INT,
    y            INT,
    w            INT,
    h            INT,
    Edad         INT,
    Es_Menor     BOOLEAN,
    Escore       DECIMAL(5,4),
    Estado       VARCHAR(50),
    FOREIGN KEY (Id_Solicitud) REFERENCES Solicitud(Id_Solicitud)
);

CREATE INDEX IF NOT EXISTS idx_solicitud_guid      ON Solicitud(GUID_Solicitud);
CREATE INDEX IF NOT EXISTS idx_solicitud_estado    ON Solicitud(Estado);
CREATE INDEX IF NOT EXISTS idx_imagenes_solicitud  ON Imagenes(Id_Solicitud);
CREATE INDEX IF NOT EXISTS idx_imagenes_num_cara   ON Imagenes(Id_Solicitud, Num_Cara);
