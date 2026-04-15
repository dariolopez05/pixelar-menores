@echo off
echo ========================================
echo  RESET COMPLETO
echo  Esto borra todos los contenedores
echo  Y TODOS LOS DATOS (BD + MinIO + Kafka)
echo ========================================
set /p confirm="Estas seguro? (s/n): "
if /i "%confirm%"=="s" (
    docker compose down -v
    echo Reset completo. Todos los datos han sido eliminados.
) else (
    echo Operacion cancelada.
)
