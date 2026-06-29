@echo off
REM Launch the Streamlit admin app
setlocal

cd /d "%~dp0"

if not exist ".venv\" (
    echo Creating virtual environment...
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)

if not exist "config.json" (
    echo config.json missing. Copying from config.json.example...
    copy config.json.example config.json
)

.venv\Scripts\python.exe -m streamlit run app\app.py

endlocal
