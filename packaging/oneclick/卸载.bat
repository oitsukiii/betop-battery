@echo off
chcp 65001 >nul
title betop-battery uninstaller
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\oneclick\uninstall.ps1"
if errorlevel 1 pause
