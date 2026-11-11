@echo off
chcp 65001 >nul
title Robo BI
cd /d "%~dp0"
echo.
echo  Robo BI aberto em http://127.0.0.1:8765
echo  Feche esta janela para encerrar.
echo.
python servidor_bi.py
pause
