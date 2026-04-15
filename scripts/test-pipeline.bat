@echo off
:: Uso: test-pipeline.bat ruta\a\imagen.jpg
:: Ejemplo: test-pipeline.bat C:\Users\Dario\Pictures\foto.jpg

if "%1"=="" (
    echo Uso: test-pipeline.bat ruta\a\imagen.jpg
    exit /b 1
)

echo ========================================
echo  Enviando imagen al pipeline...
echo  Fichero: %1
echo ========================================

curl -s -X POST http://localhost:8000/images -F "file=@%1" -o response.json

echo Respuesta:
type response.json

echo.
echo ========================================
echo  Guardando el GUID para consultar despues
echo ========================================
echo Ejecuta para consultar el resultado:
echo   curl http://localhost:8000/images/{guid_solicitud}
echo.
del response.json
