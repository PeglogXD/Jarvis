@echo off
REM ─── Inicia el listener de J.A.R.V.I.S. en segundo plano ───
REM Ejecuta este .bat al inicio de Windows para que "Hey Jarvis" funcione siempre.
REM
REM Para agregarlo al inicio de Windows:
REM   1. Presiona Win+R, escribe "shell:startup" y presiona Enter
REM   2. Copia este .bat a la carpeta que se abre
REM
REM O usa Task Scheduler para un inicio más controlado:
REM   - Acción: Iniciar programa
REM   - Programa: C:\Windows\System32\wscript.exe
REM   - Argumentos: "C:\Jarvis\start_listener.vbs"
REM
cd /d "C:\Jarvis"
start "" /min pythonw listener.py
