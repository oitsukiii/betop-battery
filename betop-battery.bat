@echo off
chcp 65001 >nul
rem ============================================================
rem  betop-battery 便捷启动脚本
rem    双击          -> 启动托盘（用 pythonw，不显示控制台窗口）
rem    带参数         -> 命令行模式（保留输出）
rem       betop-battery.bat once
rem       betop-battery.bat probe dump
rem ============================================================
setlocal
where pythonw >nul 2>nul && (set PYW=pythonw) || (set PYW=py -w)
where python  >nul 2>nul && (set PY=python)   || (set PY=py)

if "%~1"=="" (
    rem 无参数：静默启动托盘，无控制台窗口
    start "" %PYW% "%~dp0run.py" tray
    exit /b 0
)

rem 有参数：作为命令行工具使用
%PY% "%~dp0run.py" %*
