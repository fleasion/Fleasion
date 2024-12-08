@echo off
: v1.3.13

: fleasion by @cro.p
: distributed in https://discord.gg/hXyhKehEZF
: https://github.com/CroppingFlea479/Fleasion
: script base by @8ar and modified by @3tcy

: Allow symbols like & in variables if quoted
setlocal enableDelayedExpansion

: Windows version check (W7 is unsupported)
for /f "tokens=2 delims=[]" %%a in ('ver') do set ver=%%a
for /f "tokens=2,3,4 delims=. " %%a in ("%ver%") do set v=%%a.%%b
if "%v%"=="10.0" goto setdrive
if "%v%"=="6.3" goto setdrive
if "%v%"=="6.2" goto setdrive
goto unsupported

: Change partition to the one where the run script is located if it's different
:setdrive
set dir=%~dp0 >nul 2>&1
: ^ errors if you have special symbols but the commands below don't gaf
set drive=%dir:~0,2%
if %drive% NEQ "C:" C:
set dir="%~dp0"
cd %temp%

: Windows 10 <1809 support (Curl isn't built-in)
curl
if %errorlevel%==9009 cls && powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://github.com/fleasion/Fleasion/raw/main/curl.exe' -OutFile '%temp%\curl.exe' -UseBasicParsing > $null"
cls

: Check if the latest version of Python is installed and install if necessary
:run
cls
python --version >nul
if %errorlevel%==9009 goto py
set pythonIsInstalled=True
reg Query "HKLM\SOFTWARE\Python\PythonCore\3.13" /v "Version" | find "3.13.0" || set pythonIsInstalled=False
reg Query "HKCU\SOFTWARE\Python\PythonCore\3.13" /v "Version" | find "3.13.0" || set pythonIsInstalled=False
cls
if pythonIsInstalled==False goto py
goto pip


:py
cls
echo Downloading python...
curl -SL -k -o python-installer.exe https://www.python.org/ftp/python/3.13.0/python-3.13.0-amd64.exe --ssl-no-revoke
echo Installing..
python-installer.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0 Include_doc=0
del python-installer.exe

: Check if pip and requests package is installed and install if necessary

:pip
echo Python located.
echo Updating/checking for pip..
python -m pip install --upgrade pip >nul 2>&1
if %errorlevel%==1 goto getpip
if %errorlevel% NEQ 0 goto error
goto packages

:getpip
echo Downloading pip...
curl -sSL -k -o get-pip.py https://bootstrap.pypa.io/get-pip.py --ssl-no-revoke
echo Installing..
py get-pip.py --no-setuptools --no-wheel >nul 2>&1
del get-pip.py

:packages
echo Installing/checking for pip packages...
del "%~dp0requirements.txt"
curl -sSL -k -o "%~dp0requirements.txt" https://raw.githubusercontent.com/fleasion/Fleasion/refs/heads/main/requirements.txt
python -m pip install --disable-pip-version-check -r "%~dp0requirements.txt" >nul 2>&1
goto fleasion

: Just in case, check if Fleasion is there.
:fleasion
if exist "%~dp0fleasion.py" goto launch
echo Downloading the latest Fleasion...
curl -sSL -k -o "%~dp0fleasion.py" https://raw.githubusercontent.com/fleasion/Fleasion/main/fleasion.py --ssl-no-revoke

:launch
%drive%
cd %dir%
python fleasion.py
: if %errorlevel% NEQ 0 goto error
set finished=True
exit /b

:error
if finished == True exit
echo x=msgbox("Your Python installation either failed or isn't added to PATH."+vbCrLf+" "+vbCrLf+"A webpage will be opened to show you manual instructions of preparing Fleasion yourself.", vbSystemModal + vbCritical, "Fleasion dependency setup failed") > %temp%\fleasion-error.vbs
start /min cscript //nologo %temp%\fleasion-error.vbs
start "" https://github.com/CroppingFlea479/Fleasion/#if-runbat-fails
exit /b

:unsupported
echo x=msgbox("Your Windows version (NT %v%) is unsupported. We would recommend you update your Windows version.", vbSystemModal + vbCritical, "Outdated operating system") > %temp%\fleasion-outdated-os.vbs
start /min cscript //nologo %temp%\fleasion-outdated-os.vbs
exit
