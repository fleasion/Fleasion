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
python --version >> py.txt
if %errorlevel%==9009 goto py
findstr "Python 3.13" py.txt >nul 2>&1
if %errorlevel% NEQ 0 goto py
del py.txt
cls
goto pip 

:py
del py.txt >nul 2>&1
if exist python_installed.txt goto pip
cls
echo Downloading python...
curl -SL -k -o python-installer.exe https://www.python.org/ftp/python/3.13.2/python-3.13.2-amd64.exe --ssl-no-revoke
echo Installing..
python-installer.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0 Include_doc=0
del python-installer.exe
echo Installed > python_installed.txt
start "" "%~f0"
exit 

: Fleasion cannot operate without a few pip packages.
:pip
echo Checking for pip...
python -m pip >nul 2>&1
if %errorlevel% NEQ 0 python -m ensurepip --upgrade
goto fleasion

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
if exist autoupdate.py (
    python autoupdate.py
    if %errorlevel% NEQ 0 goto error
    set finished=True
)
if finished == True exit /b
goto updater_error

:error
echo x=msgbox("Python failed, is missing or isn't added to PATH."+vbCrLf+" "+vbCrLf+"You will be redirected to a discord server where you can report this issue.", vbSystemModal + vbCritical, "Fleasion dependency setup failed") > %temp%\fleasion-error.vbs
start /min cscript //nologo %temp%\fleasion-error.vbs
start "" https://discord.gg/invite/fleasion
del %temp%\fleasion-error.vbs
exit /b

:updater_error
echo x=msgbox("The updater failed to download."+vbCrLf+" "+vbCrLf+"You will be redirected to a website where you can download it manually.", vbSystemModal + vbCritical, "Fleasion dependency setup failed") > %temp%\fleasion-error.vbs
start /min cscript //nologo %temp%\fleasion-error.vbs
start "" https://github.com/qrhrqiohj/Fleasion-Backend/blob/main/autoupdate.py
del %temp%\fleasion-error.vbs
exit /b

:unsupported
echo x=msgbox("Your Windows version (NT %v%) is unsupported, not even Roblox supports it.", vbSystemModal + vbCritical, "Outdated operating system.") > %temp%\fleasion-outdated-os.vbs
start /min cscript //nologo %temp%\fleasion-outdated-os.vbs
exit

:no_internet
echo x=msgbox("No internet connection detected.", vbSystemModal + vbCritical, "Internet connectivity test failed") > %temp%\fleasion-no-internet.vbs
start /min cscript //nologo %temp%\fleasion-outdated-os.vbs
exit
