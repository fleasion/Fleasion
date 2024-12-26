@echo off
: fleasion by @cro.p, rewrite by @8ar
: distributed in https://discord.com/invite/fleasion
: https://github.com/CroppingFlea479/Fleasion
: script base by @8ar and modified by @3tcy

: Allows symbols like '&' in variables if quoted
setlocal enableDelayedExpansion

: No point of launching Fleasion if Roblox itself doesn't support the operating system
for /f "tokens=2 delims=[]" %%a in ('ver') do set ver=%%a
for /f "tokens=2,3,4 delims=. " %%a in ("%ver%") do set v=%%a.%%b
if "%v%"=="10.0" goto setdrive
if "%v%"=="6.3" goto setdrive
goto unsupported

: Change partition to the one where the run script is located if it's different
:setdrive
set dir=%~dp0 >nul 
: ^ will error if finds special symbols, ignore it
set drive=%dir:~0,2%
if %drive% NEQ "C:" C:
set dir="%~dp0"
cd %temp%

: Curl isn't built into W10 <1809
curl
if %errorlevel%==9009 (
    if not exist "%~dp0storage\curl.exe" (
        cls
        powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://github.com/fleasion/Fleasion/raw/main/curl.exe' -OutFile '%~dp0storage\curl.exe' -UseBasicParsing > $null" 
    )
    xcopy "%~dp0storage\curl.exe" "%temp%\curl.exe" >nul

)
cls

: Fleasion requires Python >=3.12
:run
cls
python --version >nul
if %errorlevel%==9009 goto py
set pythonIsInstalled=True
reg Query "HKLM\SOFTWARE\Python\PythonCore\3.13" /v "SysVersion" | find "3.13.0" || set pythonIsInstalled=False 
reg Query "HKCU\SOFTWARE\Python\PythonCore\3.13" /v "SysVersion" | find "3.13.0" || set pythonIsInstalled=False 
cls
if pythonIsInstalled==False goto py
goto pip

:py
cls
echo Downloading python...
curl -SL -k -o python-installer.exe https://www.python.org/ftp/python/3.13.1/python-3.13.1-amd64.exe --ssl-no-revoke
echo Installing..
python-installer.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0 Include_doc=0 LongPathsEnabled=1
del python-installer.exe

: Fleasion cannot operate without a few pip packages.
:pip
echo Checking for pip..
python -m pip >nul
if %errorlevel%==1 goto getpip
if %errorlevel% NEQ 0 goto error
goto fleasion

:getpip
echo Downloading pip...
curl -sSL -k -o get-pip.py https://bootstrap.pypa.io/get-pip.py --ssl-no-revoke
echo Installing..
py get-pip.py --no-setuptools --no-wheel >nul 2>&1
del get-pip.py

: Dependency packages moved to storage\auto-update.py
:                psutil, colorama

:fleasion
md "%~dp0assets\games" >nul 2>&1
md "%~dp0assets\community" >nul 2>&1
md "%~dp0assets\presets" >nul 2>&1
md "%~dp0assets\custom\storage\cached_files" >nul 2>&1
md "%~dp0storage" >nul 2>&1
if not exist "%~dp0assets\custom\storage\assets.json" (
    echo { > "%~dp0assets\custom\storage\assets.json"
    echo    "Explanation": "If you dont want custom hashes to show as unknown or a hash, define them here", >> "%~dp0assets\custom\storage\assets.json"
    echo    "Name goes Here": "Hash goes Here" >> "%~dp0assets\custom\storage\assets.json"
    echo } >> "%~dp0assets\custom\storage\assets.json"
)
if exist "%~dp0UNZIP BEFORE RUN" del "%~dp0UNZIP BEFORE RUN"
if not exist "%~dp0storage\LICENSE" (if exist "%~dp0LICENSE" (move /Y "%~dp0LICENSE" "%~dp0storage\LICENSE" >nul 2>&1) else (curl -sSL -k -o "%~dp0storage\LICENSE" https://raw.githubusercontent.com/qrhrqiohj/Fleasion-Backend/refs/heads/main/LICENSE --ssl-no-revoke))
if not exist "%~dp0storage\README.md" (if exist "%~dp0README.md" (move /Y "%~dp0README.md" "%~dp0storage\README.md" >nul 2>&1) else (curl -sSL -k -o "%~dp0storage\README.md" https://raw.githubusercontent.com/fleasion/Fleasion/refs/heads/rewrite/README.md --ssl-no-revoke))
if exist "%~dp0storage\autoupdate.py" goto launch
echo Downloading updater..
curl -sSL -k -o "%~dp0storage\autoupdate.py" https://github.com/qrhrqiohj/Fleasion-Backend/raw/main/autoupdate.py --ssl-no-revoke

:launch
%drive%
cd %dir%\storage
cls
python autoupdate.py
if %errorlevel% NEQ 0 goto error
set finished=True
exit /b

:error
if finished == True exit
echo x=msgbox("Python failed, isn't added to PATH or the Updater failed to download."+vbCrLf+" "+vbCrLf+"You will be redirected to a discord server where you can report this issue.", vbSystemModal + vbCritical, "Fleasion dependency setup failed") > %temp%\fleasion-error.vbs
start /min cscript //nologo %temp%\fleasion-error.vbs
start "" https://discord.gg/invite/fleasion
exit /b

:unsupported
echo x=msgbox("Your Windows version (NT %v%) is unsupported, not even Roblox supports it.", vbSystemModal + vbCritical, "Outdated operating system.") > %temp%\fleasion-outdated-os.vbs
start /min cscript //nologo %temp%\fleasion-outdated-os.vbs
exit
