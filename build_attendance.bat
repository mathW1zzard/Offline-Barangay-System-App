@echo off
REM Builds the ATTENDANCE EDITION (no Documents, no Staff accounts). Run from the project folder with your
REM virtual environment active. Needs:  pip install pyinstaller
REM It uses its own folders (build_attendance, dist_attendance), so it never deletes the Barangay System build.
setlocal
set BQS_EDITION=attendance

echo Cleaning the old Attendance edition build...
rmdir /s /q build_attendance 2>nul
rmdir /s /q dist_attendance 2>nul

echo Listing the open-source parts and their licenses...
python make_notices.py
if errorlevel 1 (
    echo.
    echo BUILD FAILED - make_notices.py could not make the license list.
    pause
    exit /b 1
)

echo Building...
pyinstaller --noconfirm --clean --distpath dist_attendance --workpath build_attendance\work Barangay_Attendance.spec
if errorlevel 1 (
    echo.
    echo BUILD FAILED - read the messages above.
    pause
    exit /b 1
)

echo.
echo Done. Run:  dist_attendance\Barangay_Attendance\Barangay_Attendance.exe
echo Then open installer_attendance.iss in Inno Setup and press Compile.
pause
