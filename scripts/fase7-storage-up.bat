@echo off
echo ========================================
echo  FASE 7 - Levantando Storage Service
echo ========================================

docker compose up -d --build storage-service

echo.
echo Logs de Storage Service (Ctrl+C para salir):
docker compose logs -f storage-service
