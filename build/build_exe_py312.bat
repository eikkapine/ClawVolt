@echo off
setlocal EnableDelayedExpansion
title ClawVolt — Build Executable

echo.
echo  ========================================================
echo   ClawVolt ^— Build Executable
echo  ========================================================
echo.

:: ── Must be run from the repo root (parent of build\) ───────────────────────
cd /d "%~dp0\.."

if not exist "src\claw_volt_gui.py" (
    echo  [ERROR] Run this script from the repo root or double-click from build\
    pause & exit /b 1
)

if not exist "adlx_bridge.exe" (
    if exist "bridge\build\Release\adlx_bridge.exe" (
        echo  [INFO] Copying adlx_bridge.exe from bridge\build\Release\ ...
        copy "bridge\build\Release\adlx_bridge.exe" "adlx_bridge.exe" >nul
    ) else (
        echo  [ERROR] adlx_bridge.exe not found.
        echo          Build it first: cd bridge ^&^& cmake -B build -DADLX_SDK_DIR=... -A x64
        echo                          cmake --build build --config Release
        pause & exit /b 1
    )
)

:: ── Check Python ─────────────────────────────────────────────────────────────
py -3.12 --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python 3.12 not found.
    echo          Install from https://python.org/downloads/release/python-3128/
    pause & exit /b 1
)
for /f "tokens=*" %%v in ('py -3.12 --version 2^>^&1') do set PYVER=%%v
echo  [OK] !PYVER!

:: ── Install dependencies ─────────────────────────────────────────────────────
echo.
echo  [INFO] Installing PyInstaller and pywin32 ...
py -3.12 -m pip install --upgrade pyinstaller pywin32 --quiet
if errorlevel 1 ( echo  [ERROR] pip install failed. & pause & exit /b 1 )

for /f "tokens=*" %%v in ('py -3.12 -m PyInstaller --version 2^>^&1') do set PIV=%%v
echo  [OK] PyInstaller !PIV!

:: ── Clean ────────────────────────────────────────────────────────────────────
echo.
echo  [INFO] Cleaning previous build ...
if exist "build\dist"  rmdir /s /q "build\dist"
if exist "build\build_tmp" rmdir /s /q "build\build_tmp"

:: ── Build ────────────────────────────────────────────────────────────────────
echo.
echo  [INFO] Building ClawVolt.exe ...
echo         (This takes 30-60 seconds)
echo.

py -3.12 -m PyInstaller build\ClawVolt.spec --noconfirm --workpath build\build_tmp --distpath build\dist

if errorlevel 1 (
    echo.
    echo  [ERROR] PyInstaller build failed.
    pause & exit /b 1
)

:: ── Post-build: copy adlx_bridge.exe into output ────────────────────────────
if exist "build\dist\ClawVolt\ClawVolt.exe" (
    if not exist "build\dist\ClawVolt\adlx_bridge.exe" (
        echo  [INFO] Copying adlx_bridge.exe to output folder ...
        copy "adlx_bridge.exe" "build\dist\ClawVolt\adlx_bridge.exe" >nul
    )

    echo.
    echo  ========================================================
    echo   BUILD SUCCESSFUL
    echo  ========================================================
    echo.
    echo   Output : build\dist\ClawVolt\
    echo   Run    : build\dist\ClawVolt\ClawVolt.exe  (as Administrator)
    echo.

    set /a SIZE=0
    for /r "build\dist\ClawVolt" %%f in (*) do set /a SIZE+=%%~zf
    set /a SIZE_MB=!SIZE! / 1048576
    echo   Folder size: ~!SIZE_MB! MB
    echo.

    set /p OPEN="  Open output folder? (y/n): "
    if /i "!OPEN!"=="y" explorer "build\dist\ClawVolt"
) else (
    echo  [ERROR] Output exe not found.
    pause & exit /b 1
)

echo.
pause
endlocal
