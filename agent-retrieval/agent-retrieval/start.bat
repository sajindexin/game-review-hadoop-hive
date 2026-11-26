@echo off
cd /d "%~dp0"
if not exist "data\meta.json" (
  echo [ERROR] Missing data\ directory.
  echo   - Use the full package, or
  echo   - run tools.build_knowledge first ^(see README^).
  pause
  exit /b 1
)
echo Installing dependencies...
python -m pip install -r requirements.txt
echo.
echo Server: http://127.0.0.1:8000/   API docs: /docs
python -m src.cli serve --port 8000
pause
