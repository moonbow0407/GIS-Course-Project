@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 方式1: 优先使用项目自己的 venv，确保启动的是当前源码环境。
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py
    goto :end
)

REM 方式2: venv 不存在时再尝试使用 uv。
where uv >nul 2>&1
if %errorlevel% equ 0 (
    uv run python main.py
    goto :end
)

REM 方式3: 最后尝试使用当前环境中的 Python。
call .venv\Scripts\activate
python main.py

:end
pause
