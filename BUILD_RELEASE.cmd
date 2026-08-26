@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_release.ps1" -Version 2.3.1 -SkipTutorialPdf -PatchOnly
if errorlevel 1 (
  echo 发布包生成失败。
  pause
  exit /b 1
)
echo 发布包已生成到 release 文件夹。
pause
