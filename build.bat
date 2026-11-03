@echo off
REM Builds the desktop app. Run from the project folder with your virtual environment active.
REM Needs:  pip install pyinstaller
echo Cleaning old build...
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul

echo Listing the open-source parts and their licenses...
python make_notices.py
if errorlevel 1 (
    echo.
    echo BUILD FAILED - make_notices.py could not make the license list.
    pause
    exit /b 1
)

echo Building...
pyinstaller --noconfirm --clean Barangay_System.spec
if errorlevel 1 (
    echo.
    echo BUILD FAILED - read the messages above.
    pause
    exit /b 1
)

echo.
echo Done. Run:  dist\Barangay_System\Barangay_System.exe
pause
