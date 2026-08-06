@echo off
setlocal EnableExtensions
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_support\Build-Portable.ps1"
exit /b %ERRORLEVEL%
