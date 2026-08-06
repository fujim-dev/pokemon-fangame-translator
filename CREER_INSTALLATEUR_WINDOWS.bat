@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Pokemon Fangame Translator - Build Windows
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_support\Build-Installer.ps1"
exit /b %ERRORLEVEL%
