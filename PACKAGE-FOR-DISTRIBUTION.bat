@echo off
echo ========================================
echo Capper Timer - Package Creator
echo ========================================
echo.

REM Check if dist folder exists
if not exist "dist\CapperTimer.exe" (
    echo ERROR: CapperTimer.exe not found!
    echo Please run build-exe-simple.bat first to build the executable.
    pause
    exit /b 1
)

echo Creating distribution package...
echo.

REM Create distribution folder
if exist "CapperTimer-Distribution" rmdir /s /q "CapperTimer-Distribution"
mkdir "CapperTimer-Distribution"

REM Copy files
copy "dist\CapperTimer.exe" "CapperTimer-Distribution\" >nul
copy "CapperTimer.bat" "CapperTimer-Distribution\" >nul
copy "README-DISTRIBUTION.txt" "CapperTimer-Distribution\" >nul

echo Files copied to: CapperTimer-Distribution\
echo.
echo Distribution package ready!
echo.
echo Contents:
echo   - CapperTimer.exe (the application)
echo   - CapperTimer.bat (launcher - double click this)
echo   - README-DISTRIBUTION.txt (instructions)
echo.
echo Next steps:
echo 1. Zip the CapperTimer-Distribution folder
echo 2. Share the zip file with your teammates
echo 3. They extract and double-click CapperTimer.bat
echo.
pause


