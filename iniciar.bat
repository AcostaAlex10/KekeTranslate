@echo off
REM Arranca KekeTranslate con doble clic.
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run.py %*
) else (
    echo No se encontro el entorno virtual .venv
    echo Crealo con:  python -m venv .venv
    echo Y luego:     .venv\Scripts\python.exe -m pip install -r requirements.txt
)

echo.
pause
