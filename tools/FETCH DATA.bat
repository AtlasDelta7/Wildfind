@echo off
title WildFind - fetching data
cd /d "%~dp0.."
echo.
echo  This fills WildFind with real UK data so it works offline afterwards.
echo.
echo  Stage 1  the sites       a few minutes plus the download, needs Python
echo  Stage 2  the photographs about a day, slow on purpose
echo.
echo  Stop any time with Ctrl+C. Run it again and it carries on from there.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fetch-data.ps1" %*
echo.
echo  Finished. Close this window and open WildFind.html
pause
