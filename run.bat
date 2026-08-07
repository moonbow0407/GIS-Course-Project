@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 方式1: 使用 uv（如果已安装）
where uv >nul 2>&1
if %errorlevel% equ 0 (
    uv run python main.py
    goto :end
)

REM 方式2: 直接用 venv 中的 Python
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py
    goto :end
)

REM 方式3: 激活 venv 后用 python
call .venv\Scripts\activate
python main.py

:end
pause
