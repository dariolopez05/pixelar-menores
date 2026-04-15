@echo off
echo ========================================
echo  FASE 4 - Levantando Face Detection
echo ========================================

docker compose up -d --build face-detection

echo.
echo Logs de Face Detection (Ctrl+C para salir):
docker compose logs -f face-detection
