@echo off
title ELIPSE - Launcher
cd /d "%~dp0"

echo ============================================
echo   ELIPSE - arrancando todo
echo ============================================
echo.

REM --- 1) Ollama: solo lo levanta si el puerto 11434 no esta ya en uso ---
netstat -ano | findstr :11434 >nul
if errorlevel 1 (
    echo [1/3] Ollama no estaba corriendo, levantandolo en su propia ventana...
    start "ELIPSE - Ollama" cmd /k ollama serve
    timeout /t 3 >nul
) else (
    echo [1/3] Ollama ya esta corriendo, no hace falta levantarlo de nuevo.
)

REM --- 2) uvicorn, con el venv activado, en su propia ventana ---
if not exist "venv\Scripts\activate.bat" (
    echo.
    echo ERROR: no encontre venv\Scripts\activate.bat en esta carpeta.
    echo Corre este .bat desde la raiz del proyecto ^(junto a la carpeta "app"^).
    pause
    exit /b 1
)

echo [2/3] Levantando la API de ELIPSE ^(uvicorn^)...
start "ELIPSE - API" cmd /k "call venv\Scripts\activate.bat && uvicorn app.main:app --reload"

REM --- 3) Espera un poco a que uvicorn arranque, y abre el cliente web ---
timeout /t 3 >nul
if exist "elipse_client.html" (
    echo [3/3] Abriendo el cliente web...
    start "" "elipse_client.html"
) else (
    echo [3/3] No encontre elipse_client.html en esta carpeta, saltando ese paso.
)

echo.
echo Listo. Dos ventanas quedaron abiertas ^(Ollama y la API^) - no las cierres
echo mientras uses ELIPSE. Esta ventana se puede cerrar sin problema.
echo.
pause