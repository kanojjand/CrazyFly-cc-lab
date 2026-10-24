@echo off
rem Builds ONE file: dist\CC Lab CrazyFly.exe (program + Python + fonts + Crazyradio driver inside).
rem Needs: .venv (run start.bat once).
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo .venv not found. Run start.bat once first - it creates the environment.
    goto :error
)
"%PY%" -m pip install -q -r requirements-build.txt || goto :error
echo [1/3] Tests...
"%PY%" -m pytest -q || goto :error
echo [2/3] Icon...
"%PY%" installer\make_icon.py || goto :error
echo [3/3] PyInstaller...
rem Old multi-file build (folder) - replaced by the single exe.
if exist "dist\CC Lab CrazyFly\" rmdir /s /q "dist\CC Lab CrazyFly"
"%PY%" -m PyInstaller --noconfirm --clean --log-level WARN --distpath dist --workpath build installer\CrazyflyPilot.spec || goto :error
echo Done: dist\CC Lab CrazyFly.exe
exit /b 0

:error
echo Build failed, see the messages above.
pause
exit /b 1
