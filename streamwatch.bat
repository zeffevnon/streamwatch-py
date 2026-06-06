@echo off
where pythonw >nul 2>&1
if errorlevel 1 (
    echo Python was not found.
    echo Download and install it from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)
start "" pythonw "%~dp0gui.pyw"
