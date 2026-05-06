@echo off
REM ============================================================
REM  FAM Market Manager - Build standalone .exe
REM  Run this script from the project root directory.
REM
REM  RELEASE GATE REMINDER
REM  ---------------------
REM  Before tagging and distributing this build, the full
REM  Release Audit Gate MUST pass:
REM
REM      scripts\run_release_audit.bat
REM
REM  See docs\RELEASE_AUDIT_PROCEDURE.md.  Three gates run in
REM  ~90 seconds total.  If you skipped them, stop now and run
REM  them.  A clean build is not a substitute for clean audits.
REM
REM  Pass /skip-audit-prompt to suppress the interactive prompt
REM  in CI scenarios where the audit was already gated upstream.
REM ============================================================

echo.
echo ========================================
echo  FAM Market Manager - Build Script
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

REM ------------------------------------------------------------
REM Release-gate reminder (interactive unless suppressed)
REM ------------------------------------------------------------
if /I "%~1"=="/skip-audit-prompt" goto :skip_audit_prompt
echo.
echo ============================================================
echo  RELEASE AUDIT GATE REMINDER
echo ============================================================
echo  Have you run the full Release Audit Gate against this
echo  source tree?
echo.
echo      scripts\run_release_audit.bat
echo.
echo  This is mandatory for every release.  See
echo  docs\RELEASE_AUDIT_PROCEDURE.md.
echo ============================================================
set /p _AUDIT_OK="  Audit passed? (Y to continue, N to abort): "
if /I not "%_AUDIT_OK%"=="Y" (
    echo.
    echo  Build aborted.  Run scripts\run_release_audit.bat first.
    exit /b 1
)
:skip_audit_prompt
echo.

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
