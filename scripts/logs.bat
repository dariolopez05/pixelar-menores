@echo off
:: Uso: logs.bat [nombre-servicio]
:: Ejemplo: logs.bat api-gateway
:: Sin argumento muestra todos los servicios

if "%1"=="" (
    echo Mostrando logs de todos los servicios (Ctrl+C para salir)
    docker compose logs -f
) else (
    echo Mostrando logs de: %1 (Ctrl+C para salir)
    docker compose logs -f %1
)
