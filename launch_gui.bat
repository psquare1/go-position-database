@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python was not found. Install Python 3, then run this file again.
        pause
        exit /b 1
    )
    set "BOOTSTRAP_PYTHON=python"
) else (
    set "BOOTSTRAP_PYTHON=py"
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating the local Python environment...
    %BOOTSTRAP_PYTHON% -m venv .venv
    if errorlevel 1 goto :error
)

echo Checking application packages...
".venv\Scripts\python.exe" -m pip install -r requirements-gui.txt
if errorlevel 1 goto :error

".venv\Scripts\python.exe" go_db.py --root "%CD%" init
if errorlevel 1 goto :error

".venv\Scripts\python.exe" go_db.py --root "%CD%" gui
if errorlevel 1 goto :error
endlocal
exit /b 0

:error
echo.
echo Go Position DB could not be started. See the message above for details.
pause
endlocal
exit /b 1
