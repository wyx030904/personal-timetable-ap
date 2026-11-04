@echo off
chcp 65001 >nul
cd /d %~dp0

rem 依次尝试：项目内 venv → 已装好 paddle 的隔离环境 → 系统 python
if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else if exist "C:\Users\harry\.workbuddy\binaries\python\envs\default\Scripts\python.exe" (
  set "PY=C:\Users\harry\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
) else (
  set "PY=python"
)

"%PY%" app.py
pause
