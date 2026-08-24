@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\pyinstaller.exe" (
  echo 请先安装开发依赖：.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
  pause
  exit /b 1
)
".venv\Scripts\pyinstaller.exe" --noconfirm --clean YanxiangExpenseV2.spec
