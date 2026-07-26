@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 goto use_python

py -3 run_app.py %*
goto end

:use_python
python run_app.py %*

:end
endlocal
