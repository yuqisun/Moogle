@echo off
REM Moogle launcher for Windows CMD
REM Starts Streamlit chat (8501) + static file server with Range support (8502)

setlocal enabledelayedexpansion

set "ROOT=%~dp0"
cd /d "%ROOT%"

REM --- Detect Python / venv ---
if exist "%ROOT%.venv\Scripts\python.exe" (
    set "PY=%ROOT%.venv\Scripts\python.exe"
    set "STREAMLIT=%ROOT%.venv\Scripts\streamlit.exe"
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo ERROR: No Python found. Install Python 3.8+ and create a venv:
        echo   python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
        exit /b 1
    )
    set "PY=python"
    where streamlit >nul 2>&1
    if errorlevel 1 (
        echo ERROR: streamlit not found. Install it:
        echo   python -m pip install -r requirements.txt
        exit /b 1
    )
    set "STREAMLIT=streamlit"
)

REM --- Read STATIC_PORT from .env (default 8502) ---
set "STATIC_PORT=8502"
set "STREAMLIT_PORT=8501"
if exist "%ROOT%.env" (
    for /f "usebackq tokens=1,* delims==" %%a in ("%ROOT%.env") do (
        set "line=%%a"
        if not "!line:~0,1!"=="#" (
            if "%%a"=="STATIC_PORT" set "STATIC_PORT=%%b"
            if "%%a"=="STREAMLIT_PORT" set "STREAMLIT_PORT=%%b"
        )
    )
)

REM --- Check if ports are already in use ---
for %%p in (%STREAMLIT_PORT% %STATIC_PORT%) do (
    netstat -ano | findstr "LISTENING" | findstr ":%%p " >nul 2>&1
    if not errorlevel 1 (
        for /f "tokens=5" %%i in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":%%p "') do (
            echo ERROR: Port %%p is already in use by PID %%i.
            echo        Stop the existing process or close the other Moogle instance first.
            exit /b 1
        )
    )
)

REM --- Start static file server (background) ---
echo Starting static file server on port %STATIC_PORT% (Range requests enabled) ...
start /b "" "%PY%" "%ROOT%static_server.py" %STATIC_PORT% --bind 127.0.0.1 --directory "%ROOT%static"

REM Give it a moment to start
timeout /t 2 /nobreak >nul

REM --- Start Streamlit (foreground) ---
echo Starting Streamlit on port %STREAMLIT_PORT% ...
echo.
echo   URL: http://127.0.0.1:%STREAMLIT_PORT%
echo.

"%STREAMLIT%" run streamlit_app.py --server.port %STREAMLIT_PORT% --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false

REM --- Cleanup: kill static server when Streamlit exits ---
for /f "tokens=2" %%i in ('tasklist /fi "WINDOWTITLE eq *static_server*" /nh 2^>nul') do (
    taskkill /pid %%i /f >nul 2>&1
)
REM Fallback: find python processes running static_server.py
for /f "tokens=2" %%i in ('wmic process where "commandline like '%%static_server.py%%'" get processid /value 2^>nul ^| findstr "="') do (
    taskkill /pid %%i /f >nul 2>&1
)
echo Stopped static server.
