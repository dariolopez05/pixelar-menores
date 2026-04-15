@echo off
echo ========================================
echo  FASE 1 - Levantando infraestructura
echo  Kafka, PostgreSQL, MinIO
echo ========================================

docker compose up -d kafka postgres minio

echo.
echo Esperando a que los servicios esten healthy...
docker compose up -d kafka-init minio-init

echo.
echo Estado de los contenedores:
docker compose ps kafka postgres minio kafka-init minio-init
