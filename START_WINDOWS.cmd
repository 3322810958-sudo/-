@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "燕翔车队经费管理系统.exe" (
  start "" "燕翔车队经费管理系统.exe"
  exit /b 0
)
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" -m app.desktop
  exit /b 0
)
echo 尚未安装运行环境，请先双击 INSTALL_WINDOWS.cmd
pause
