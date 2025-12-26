@echo off
echo Building Capper Timer executable...
echo.

REM Clean previous builds
if exist "dist" rmdir /s /q "dist"
if exist "build" rmdir /s /q "build"
if exist "*.spec" del /q "*.spec"

REM Build with PyInstaller
pyinstaller --onefile ^
    --windowed ^
    --name "CapperTimer" ^
    --icon=NONE ^
    --hidden-import PyQt5.QtCore ^
    --hidden-import PyQt5.QtGui ^
    --hidden-import PyQt5.QtWidgets ^
    --hidden-import keyboard ^
    --hidden-import win32gui ^
    --hidden-import win32con ^
    --hidden-import websockets ^
    --hidden-import asyncio ^
    --collect-all websockets ^
    --collect-all PyQt5 ^
    main.py

echo.
echo Build complete! Check the 'dist' folder for CapperTimer.exe
pause


