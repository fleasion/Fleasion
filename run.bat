@echo off
REM v1.1.0
setlocal

REM Step 0: Set the current directory to the script's directory
cd /d "%~dp0"

REM Step 1: Check if Python is installed and update/install if necessary
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python is not installed. Installing the latest version...
    REM Download the latest Python installer
    curl -o python-installer.exe https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe
    REM Install Python silently
    python-installer.exe /quiet InstallAllUsers=1 PrependPath=1
    REM Cleanup installer
    del python-installer.exe
    echo Python is now installed, please restart!
    endlocal
    pause
    cmd /k
) else (
    echo Python is installed. Checking version...
    for /f "delims=" %%i in ('python -c "import platform; print(platform.python_version())"') do set PYTHON_VERSION=%%i
    echo Current Python version: %PYTHON_VERSION%
    REM Check if the installed version is the latest
    set LATEST_PYTHON_VERSION=3.12.4
    if not "%PYTHON_VERSION%"=="%LATEST_PYTHON_VERSION%" (
        echo Python is not the latest version. Updating...
        REM Download the latest Python installer
        curl -o python-installer.exe https://www.python.org/ftp/python/%LATEST_PYTHON_VERSION%/python-%LATEST_PYTHON_VERSION%-amd64.exe
        REM Install Python silently
        python-installer.exe /quiet InstallAllUsers=1 PrependPath=1
        REM Cleanup installer
        del python-installer.exe
        echo Python is now installed, please restart!
        endlocal
        pause
        cmd /k
    ) else (
        echo Python is up to date.
    )
)

REM Step 2: Check if pip is installed and update/install if necessary
python -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo pip is not installed. Installing pip...
    python -m ensurepip
) else (
    echo pip is installed. Updating pip...
    python -m pip install --upgrade pip
)

REM Step 3: Check if requests package is installed and install if necessary
python -c "import requests" >nul 2>&1
if %errorlevel% neq 0 (
    echo requests package is not installed. Installing requests...
    python -m pip install requests
) else (
    echo requests package is installed.
)

REM Step 4: Run the Python script fleasion.py
cls
echo Welcome to Fleasion!
python "%~dp0fleasion.py"

endlocal
pause
