@echo off
echo ========================================
echo  FASE 5 - Levantando Age Detection
echo  (primera build puede tardar ~10 min,
echo   descarga modelos de DeepFace ~500MB)
echo ========================================

docker compose up -d --build age-detection

echo.
echo Logs de Age Detection (Ctrl+C para salir):
docker compose logs -f age-detection
