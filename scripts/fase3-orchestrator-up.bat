@echo off
echo ========================================
echo  FASE 3 - Levantando Orquestadores 1-4
echo ========================================

docker compose up -d --build orchestrator-1 orchestrator-2 orchestrator-3 orchestrator-4

echo.
echo Logs de los Orquestadores (Ctrl+C para salir):
docker compose logs -f orchestrator-1 orchestrator-2 orchestrator-3 orchestrator-4
