@echo off
REM ===========================================================================
REM JARVIS — Windows Executable (.exe) Builder
REM ===========================================================================
REM Run this on Windows with Python installed to compile dist\jarvis.exe
REM ===========================================================================

set REPO=%~dp0..
cd /d "%REPO%"

echo Checking for Python virtual environment...
if exist ".venv\Scripts\python.exe" (
    set PY=.venv\Scripts\python.exe
    set PYINSTALLER=.venv\Scripts\pyinstaller.exe
) else (
    set PY=python
    set PYINSTALLER=pyinstaller
)

echo Installing PyInstaller if needed...
"%PY%" -m pip install pyinstaller Pillow

echo Generating icons if needed...
if not exist "assets\icons\jarvis.ico" (
    "%PY%" scripts\generate_icons.py
)

echo Building JARVIS Windows Executable (.exe)...
"%PYINSTALLER%" --clean -y jarvis.spec

if exist "dist\jarvis.exe" (
    echo.
    echo ==================================================
    echo Build Successful: dist\jarvis.exe
    echo ==================================================
) else (
    echo.
    echo Build failed. Check the error messages above.
)
pause
