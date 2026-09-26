@echo off
title opencode-web - the open source AI coding agent
cd /d "%~dp0"
echo.
echo   opencode-web 2.0
echo   ---------------------------------------------
echo   Starting server on http://127.0.0.1:7791
echo   Opening browser...
echo.
start "" http://127.0.0.1:7791
python server.py
pause
