@echo off
chcp 65001 >nul
rem 便捷启动：双击 = 启动托盘；带参数 = 当命令行用
rem   betop-battery.bat once
rem   betop-battery.bat probe dump
set PY=python
where python >nul 2>nul || set PY=py
"%PY%" "%~dp0run.py" %*
if "%~1"=="" pause
