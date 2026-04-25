@echo off
REM Launch the Streamlit admin app
setlocal

cd /d "%~dp0"

if not exist ".venv\" (
    echo Creating virtual environment...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install --upgrade pip
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

if not exist "config.json" (
    echo config.json missing. Copying from config.json.example...
    copy config.json.example config.json
)

streamlit run app\app.py

endlocal
