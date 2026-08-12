@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title CPS 智能代理系统

echo ==============================================
echo          CPS 智能代理系统 - Windows版
echo ==============================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 start_windows.py
    goto :end
)

where python >nul 2>nul
if %errorlevel%==0 (
    python start_windows.py
    goto :end
)

echo [错误] 没有检测到 Python。
echo.
echo 请先安装 Python 3.12：
echo https://www.python.org/downloads/windows/
echo.
echo 安装时请务必勾选：Add python.exe to PATH
echo 安装完成后，再双击“启动系统.bat”。
echo.
pause
exit /b 1

:end
if not %errorlevel%==0 (
    echo.
    echo 系统启动失败，错误代码：%errorlevel%
    echo 请把这个窗口的报错截图发给我。
    pause
)
endlocal
