@echo off
REM Mia's Arabic Screen Translator - double-click launcher (Windows)
cd /d "%~dp0"
title Mia's Arabic Screen Translator

REM --- Find Python ---
where python >nul 2>nul
if %errorlevel%==0 (
    set "PY=python"
    goto have_python
)
where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py"
    goto have_python
)
echo.
echo Python was not found on this computer.
echo.
echo Install the latest Python from https://www.python.org/downloads/
echo IMPORTANT: on the first install screen, tick the box
echo "Add python.exe to PATH", then finish the install and
echo double-click this file again.
echo.
pause
exit /b 1

:have_python
REM --- Make sure the extra pieces are installed ---
echo Checking the extra pieces (Pillow, deep-translator, pytesseract)...
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Installing the extra pieces failed. Check your internet
    echo connection, then double-click this file again.
    echo.
    pause
    exit /b 1
)

REM --- Run the app ---
%PY% translator_app.py
if errorlevel 1 (
    echo.
    echo The app closed with an error. Take a screenshot of any
    echo message above and show it to Jeff.
    echo.
    pause
)
