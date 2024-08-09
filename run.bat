@echo off
REM v1.3.2

: fleasion by @cro.p
: distributed in https://discord.gg/v9gXTuCz8B
: https://github.com/CroppingFlea479/Fleasion
: script base by @8ar and modified by @3tcy


: Check if the latest version of Python is installed and install if necessary


set LM=True & set CU=True
reg Query "HKLM\SOFTWARE\Python\PythonCore\3.12" /v "Version" | find "3.12.4" || set LM=False
reg Query "HKCU\SOFTWARE\Python\PythonCore\3.12" /v "Version" | find "3.12.4" || set CU=False
if %LM% == True goto pip
if %CU% == True goto pip
goto py

:py
cls
echo Downloading python...
curl -sSL -o python.exe https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe
echo Installing..
python.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0 Include_doc=0
del python.exe >nul


: Check if pip and requests package is installed and install if necessary


:pip
cls
echo Updating/checking for pip..
py -m pip install --upgrade pip >nul 2>&1
if %errorlevel%==1 (
    cls
    echo Downloading pip...
    curl -sSL -o get-pip.py https://bootstrap.pypa.io/get-pip.py
    echo Installing..
    py get-pip.py --no-setuptools --no-wheel >nul 2>&1
    del get-pip.py
)
if %errorlevel%==9009 goto error

cls
echo Installing/checking for requests package...
py -m pip install requests >nul 2>&1


: Just in case, check if Fleasion is there.


cls
if exist %cd%\fleasion.py goto launch
curl -sSL -o %cd%\fleasion.py https://raw.githubusercontent.com/CroppingFlea479/Fleasion/main/fleasion.py --ssl-no-revoke

:launch
py %cd%\fleasion.py if %errorlevel%==9009 goto error
exit /b

:error
echo x=msgbox("Your Python installation either failed or isn't added to PATH." + vbCrLf + " " + vbCrLf + "This is usually fixable by just restarting the 'run.bat' file or right clicking it and running is as Administrator."  + vbCrLf + " " + vbCrLf + "Please uninstall Python by searching 'Add or remove programs', pressing enter, finding Python and removing every installation you find one by one and rerun the run.bat file." + vbCrLf + " " + vbCrLf + "If Python fails to uninstall, download Bull Crap Uninstaller and uninstall Python by right clicking and selecting the 'Uninstall Manually' option.", 16, "Fleasion dependency setup failed") > %cd%\error.vbs
cscript //nologo %cd%\error.vbs
del error.vbs
exit
