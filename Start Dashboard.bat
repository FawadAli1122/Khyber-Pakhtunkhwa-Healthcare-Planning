@echo off
REM Single-click launcher: starts the server (which also starts the
REM bundled local database via server/app.py's lifespan - see
REM scripts/lib/local_db.py) and opens the dashboard in your browser
REM automatically once it's ready. No manual steps required.
REM
REM Closing the server's console window stops the server. Note: force-
REM closing it does not gracefully shut the bundled database down (see
REM docs/superpowers/specs/2026-08-16-bundled-local-database-design.md)
REM - it keeps running in the background and is safely reused next time
REM you launch.
cd /d "%~dp0"

echo Starting KP Healthcare Plan server (this also starts the bundled database)...
start "KP Healthcare Plan Server - close this window to stop" cmd /k python -m server

echo Waiting for the server to be ready (first run may take longer while the database initializes)...
set /a TRIES=0
:wait
set /a TRIES+=1
if %TRIES% gtr 120 (
    echo Server did not respond after 2 minutes - check the server window for errors.
    pause
    exit /b 1
)
timeout /t 1 /nobreak >nul
curl -s -o nul http://127.0.0.1:8420/ 2>nul
if errorlevel 1 goto wait

echo Server is ready - opening the dashboard...
start "" http://127.0.0.1:8420/
