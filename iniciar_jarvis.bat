@echo off
echo ==========================================
echo   J.A.R.V.I.S. — Iniciando...
echo ==========================================
echo.
echo [1/2] Iniciando proxy API en localhost:5050...
start /min pythonw "%~dp0proxy.py"
timeout /t 2 /nobreak >nul
echo [2/2] Abriendo JARVIS.html en el navegador...
start "" "%~dp0JARVIS.html"
echo.
echo J.A.R.V.I.S. está listo.
echo Para cerrar, cierra esta ventana y el ícono del proxy en la bandeja.
pause
