@echo off
REM ============================================================
REM  FAM Market Manager — Build standalone .exe
REM  Run this script from the project root directory.
REM ============================================================

echo.
echo ========================================
echo  FAM Market Manager — Build Script
echo ========================================
echo.

REM Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not on PATH.
    echo         Install Python 3.11+ from https://python.org
    pause
    exit /b 1
)

echo [1/4] Creating build virtual environment...
if not exist "build_venv" (
    python -m venv build_venv
)

echo [2/4] Installing dependencies...
call build_venv\Scripts\activate.bat
pip install --quiet --upgrade pip
pip install --quiet pyinstaller
pip install --quiet -r requirements.txt

echo [3/4] Running PyInstaller...
pyinstaller --clean --noconfirm fam_manager.spec

echo [4/4] Build complete!
echo.
echo ========================================
echo  Output:  dist\FAM Manager\FAM Manager.exe
echo ========================================
echo.
echo  To distribute: zip the "dist\FAM Manager" folder
echo  and share it. Users extract and double-click the .exe.
echo.

pause
