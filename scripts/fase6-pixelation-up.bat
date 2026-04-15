@echo off
echo ========================================
echo  FASE 6 - Levantando Pixelation
echo ========================================

docker compose up -d --build pixelation

echo.
echo Logs de Pixelation (Ctrl+C para salir):
docker compose logs -f pixelation
