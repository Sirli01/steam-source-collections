@echo off
cd /d "%~dp0"
rem Launch the graphical tool without a console window.
python -c "import sys" >nul 2>&1
if errorlevel 1 goto nopython
set "RUNNER="
where pythonw >nul 2>&1 && set "RUNNER=pythonw"
if not defined RUNNER where pyw >nul 2>&1 && set "RUNNER=pyw"
if defined RUNNER (
  start "" %RUNNER% -m steam_source_collections.gui
) else (
  rem No windowless Python available: keep the console minimised.
  start "" /MIN python -m steam_source_collections.gui
)
exit /b 0

:nopython
echo.
echo   Python 3.9+ is required but was not found in PATH.
echo   Install it from https://www.python.org/ and tick "Add Python to PATH",
echo   then double-click this launcher again.
echo.
pause
exit /b 1
