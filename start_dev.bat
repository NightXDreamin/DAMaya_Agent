@echo off
title DAMaya Agent [DEV MODE]
echo ============================================
echo  DAMaya Agent - Development Mode
echo  Server: http://127.0.0.1:8000
echo  Ctrl+C to stop
echo ============================================
echo.
cd /d "%~dp0"
python server_web.py
pause
