: fleasion by @cro.p
: distributed in https://discord.gg/v9gXTuCz8B
: https://github.com/CroppingFlea479/Fleasion
: script base by @8ar and modified by @3tcy

@echo off

: Check if the latest version of Python is installed and install if necessary

python --version >nul 2>&1
if %errorlevel% neq 0 goto py

for /f "delims=" %%i in ('python -c "import platform; print(platform.python_version())"') do set python_version=%%i
set latestver=3.12.4 
if not installedver==latestver goto py

goto pip

:py
echo Installing python..
curl -sSL -o python.exe https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe
python.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0 Include_doc=0
del python.exe >nul



: Check if pip is installed and install if necessary

:pip
py -m pip install --upgrade pip
if %errorlevel% neq 0 (
    echo How on green's earth do you not have Pip installed? Installing..
    curl -sSL -o get-pip.py https://bootstrap.pypa.io/get-pip.p 
    py get-pip.py --no-setuptools --no-wheel --no-warn-script-location  
    del get-pip.py >nul
    py -m pip install --upgrade pip
)



: Check if requests package is installed and install if necessary

echo Installing requests package..
py -c "import requests" >nul 2>&1
if %errorlevel% neq 0 py -m pip install requests



: Just in case, check if Fleasion is there.

if exist %cd%\fleasion.py py %cd%\fleasion.py && exit
curl -sSL -o %cd%\fleasion.py https://raw.githubusercontent.com/CroppingFlea479/Fleasion/main/fleasion.py
py %cd%\fleasion.py



