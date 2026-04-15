@echo off
echo ========================================
echo  Estado de todos los contenedores
echo ========================================
docker compose ps

echo.
echo ========================================
echo  Topics de Kafka
echo ========================================
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list 2>nul || echo [Kafka no disponible]

echo.
echo ========================================
echo  Tablas de PostgreSQL
echo ========================================
docker exec postgres psql -U faceuser -d facedb -c "SELECT Estado, COUNT(*) FROM Solicitud GROUP BY Estado;" 2>nul || echo [PostgreSQL no disponible]

echo.
echo ========================================
echo  Ultimas 5 solicitudes
echo ========================================
docker exec postgres psql -U faceuser -d facedb -c "SELECT Id_Solicitud, GUID_Solicitud, Estado, Inicio_Solicitud FROM Solicitud ORDER BY Id_Solicitud DESC LIMIT 5;" 2>nul || echo [PostgreSQL no disponible]
