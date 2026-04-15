@echo off
echo ========================================
echo  Parando todos los contenedores
echo ========================================
docker compose down
echo Contenedores parados. Los datos siguen guardados en los volumenes.
