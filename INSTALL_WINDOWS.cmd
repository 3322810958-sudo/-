@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/4] 检查 Python 3.12 运行环境...
py -3.12 -c "import sys; assert sys.maxsize > 2**32" >nul 2>nul
if errorlevel 1 (
  echo 未检测到 64 位 Python 3.12，正在尝试通过 winget 安装...
  winget install --id Python.Python.3.12 -e --scope user
)
py -3.12 -c "import sys; assert sys.maxsize > 2**32" >nul 2>nul
if errorlevel 1 (
  echo Python 安装未完成。请安装 64 位 Python 3.12 后重新运行本程序。
  pause
  exit /b 1
)
echo [2/4] 创建独立运行环境...
if not exist ".venv\Scripts\python.exe" py -3.12 -m venv .venv
echo [3/4] 安装系统与离线 OCR 组件，首次安装时间较长...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements-core.txt
".venv\Scripts\python.exe" -m pip install paddlepaddle==3.3.1 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
".venv\Scripts\python.exe" -m pip install paddleocr==3.7.0
if errorlevel 1 (
  echo 安装失败，请检查网络后重试。
  pause
  exit /b 1
)
echo [4/4] 预下载本地 OCR 模型...
".venv\Scripts\python.exe" scripts\prefetch_ocr_models.py
echo 安装完成，正在启动软件。
start "" ".venv\Scripts\pythonw.exe" -m app.desktop
