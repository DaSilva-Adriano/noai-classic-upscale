@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "APP=noai_classic_upscale.py"

set "UVPY=%USERPROFILE%\AppData\Roaming\uv\python\cpython-3.12-windows-x86_64-none\python.exe"
if exist "%UVPY%" (
  "%UVPY%" -c "import tkinter" >nul 2>&1
  if not errorlevel 1 (
    "%UVPY%" "%APP%" %*
    exit /b %ERRORLEVEL%
  )
)

where uv >nul 2>&1
if not errorlevel 1 (
  uv run --python 3.12 python -c "import tkinter" >nul 2>&1
  if not errorlevel 1 (
    uv run --python 3.12 python "%APP%" %*
    exit /b %ERRORLEVEL%
  )
)

where py >nul 2>&1
if not errorlevel 1 (
  py -3 -c "import tkinter" >nul 2>&1
  if not errorlevel 1 (
    py -3 "%APP%" %*
    exit /b %ERRORLEVEL%
  )
)

echo Could not find a Python with tkinter.
echo Install Python 3, or run:
echo   uv python install 3.12
echo   uv run --python 3.12 python %APP%
pause
exit /b 1
