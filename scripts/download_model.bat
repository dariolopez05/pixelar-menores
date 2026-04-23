@echo off
REM Descarga el modelo entrenado desde GitHub Releases.
REM Uso: scripts\download_model.bat
REM
REM Antes de usar este script:
REM   1. Entrena el modelo con: docker run --rm ...  (ver training/Dockerfile)
REM   2. Crea una release en GitHub y adjunta age-detection/model/age_classifier.keras
REM   3. Actualiza MODEL_URL abajo con la URL directa del asset

set MODEL_URL=https://github.com/dariolopez05/pixelar-menores/releases/download/v1.0-model/age_classifier.keras
set MODEL_DIR=%~dp0..\age-detection\model

if not exist "%MODEL_DIR%" mkdir "%MODEL_DIR%"

if exist "%MODEL_DIR%\age_classifier.keras" (
    echo [OK] El modelo ya existe en age-detection\model\age_classifier.keras
    echo      Borra el archivo manualmente si quieres descargarlo de nuevo.
    exit /b 0
)

echo Descargando modelo desde GitHub Releases...
curl -L -o "%MODEL_DIR%\age_classifier.keras" "%MODEL_URL%"

if %errorlevel% neq 0 (
    echo [ERROR] Fallo la descarga. Comprueba que MODEL_URL es correcto.
    echo         URL: %MODEL_URL%
    exit /b 1
)

echo [OK] Modelo descargado en age-detection\model\age_classifier.keras
echo      Ya puedes ejecutar: docker compose up -d --build age-detection
