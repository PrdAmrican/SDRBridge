@echo off
rem ======================================================================
rem  start-bridge.bat - start SDRBridge (nrsp_bridge.py) on Windows
rem  Copyright (C) 2026 PrdAmrican and SDRBridge contributors
rem  SPDX-License-Identifier: AGPL-3.0-only  (see LICENSE)
rem
rem  Double-click it, or run it from a Command Prompt. Anything you add
rem  after the name is passed on to the bridge, e.g.
rem      start-bridge.bat --antenna "Antenna B" -v
rem
rem  Before starting: SDRconnect must be running, with the nRSP-ST
rem  selected and its WebSocket server switched on (Other Actions).
rem ======================================================================
setlocal EnableExtensions
title SDRBridge
cd /d "%~dp0"

rem ---------------------------- your settings ---------------------------
rem  Serial number of the radio (shown by:  python nrsp_bridge.py --probe).
rem  The bridge selects it in Full IQ mode. Leave empty to use whatever
rem  SDRconnect has selected.
set "SERIAL=2405171650"

rem  Antenna: Antenna A, Antenna B or Antenna C. Empty = leave as it is.
set "ANTENNA=Antenna A"

rem  Where SDRconnect's WebSocket server is (host:port).
set "SDRCONNECT=127.0.0.1:5454"

rem  rtl_tcp port that SDR++ / SDR Console / SDR# connect to.
set "RTL_PORT=1234"

rem  Any other bridge options, e.g. --digital-gain 24  (see --help).
set "EXTRA="
rem ----------------------------------------------------------------------

rem --- find Python ---
set "PY="
python --version >nul 2>nul && set "PY=python"
if not defined PY (
    py -3 --version >nul 2>nul && set "PY=py -3"
)
if not defined PY (
    echo.
    echo  Python was not found. Install Python 3 from https://www.python.org/downloads/
    echo  and tick "Add python.exe to PATH" during installation.
    goto :fail
)

rem --- make sure the two libraries are installed ---
%PY% -c "import numpy, websockets" >nul 2>nul
if errorlevel 1 (
    echo Installing the required Python packages ^(numpy, websockets^)...
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 goto :fail
)

rem --- is an old copy of the bridge (or anything else) holding the port? ---
set "OLDPID="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%RTL_PORT% .*LISTENING"') do set "OLDPID=%%p"
if defined OLDPID (
    echo.
    echo  Port %RTL_PORT% is already in use by this program:
    tasklist /fi "PID eq %OLDPID%" /fo table /nh
    echo  This is usually an earlier copy of the bridge still running.
    choice /c YN /m "  Stop it and continue"
    if errorlevel 2 goto :fail
    taskkill /pid %OLDPID% /f >nul
    timeout /t 1 /nobreak >nul
)

rem --- can we reach SDRconnect's WebSocket server? (warning only) ---
%PY% -c "import socket,sys; h,p=sys.argv[1].rsplit(':',1); socket.create_connection((h.strip('[]'),int(p)),3)" %SDRCONNECT% >nul 2>nul
if errorlevel 1 (
    echo.
    echo  WARNING: nothing answers at %SDRCONNECT%.
    echo  Start SDRconnect, select the nRSP-ST and switch on its WebSocket
    echo  server ^(Other Actions^). The bridge keeps retrying until it is there.
    echo.
)

rem --- build the command line and start ---
set "ARGS=-s %SDRCONNECT% -p %RTL_PORT%"
if defined SERIAL set "ARGS=%ARGS% --device-serial %SERIAL%"
if defined ANTENNA set "ARGS=%ARGS% --antenna "%ANTENNA%""

echo.
echo  Starting SDRBridge. In SDR++ choose RTL-TCP, 127.0.0.1, port %RTL_PORT%.
echo  Press Ctrl+C to stop.
echo  SDRBridge is free software with NO WARRANTY - use at your own risk (DISCLAIMER.md).
echo.
%PY% nrsp_bridge.py %ARGS% %EXTRA% %*

echo.
echo  SDRBridge has stopped.
pause
exit /b 0

:fail
echo.
pause
exit /b 1
