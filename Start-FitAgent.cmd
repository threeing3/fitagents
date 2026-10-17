@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-fitagent.ps1" %*
if errorlevel 1 pause
