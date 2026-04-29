@echo off
title Costco Hotdeal Automation - Initializing...
echo.
echo  ================================================
echo   Costco Hotdeal Automation System
echo  ================================================
echo.

:: 1. Check if .venv exists and activate
if exist ".venv\Scripts\activate" (
    echo [INFO] Activating virtual environment...
    call .venv\Scripts\activate
)

:: 2. Check if streamlit is available
streamlit --version >nul 2>&1
if errorlevel 1 (
    echo [INFO] Streamlit not found. Attempting to install requirements...
    python -m pip install --upgrade pip
    python -m pip install streamlit pandas requests requests-toolbelt openpyxl xlsxwriter plotly bcrypt pybase64 xlwt
    
    :: Re-check after installation
    streamlit --version >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Failed to install or run Streamlit.
        echo [INFO] Trying to run via 'python -m streamlit'...
        python -m streamlit run app.py
        if errorlevel 1 (
            echo.
            echo [CRITICAL ERROR] Python might not be installed or in PATH.
            echo Please visit https://www.python.org and install Python 3.10 or higher.
            echo Make sure to check "Add Python to PATH" during installation.
            pause
            exit /b 1
        )
        exit /b 0
    )
)

echo [INFO] Starting application...
streamlit run app.py

if errorlevel 1 (
    echo.
    echo [INFO] Application crashed. Retrying with 'python -m streamlit'...
    python -m streamlit run app.py
)

pause
