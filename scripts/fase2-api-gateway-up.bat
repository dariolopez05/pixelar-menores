@echo off
echo ========================================
echo  FASE 2 - Levantando API Gateway
echo ========================================

docker compose up -d --build api-gateway

echo.
echo Logs del API Gateway (Ctrl+C para salir):
docker compose logs -f api-gateway
