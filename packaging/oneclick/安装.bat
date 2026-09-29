@echo off
chcp 65001 >nul
title betop-battery installer
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\oneclick\install.ps1"
if errorlevel 1 pause
