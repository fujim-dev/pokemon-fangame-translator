#requires -Version 5.1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Write-Host "Utilise CREER_INSTALLATEUR_WINDOWS.bat pour fabriquer le Setup.exe complet." -ForegroundColor Cyan
Write-Host "Ce script portable est conserve pour les developpeurs avances." -ForegroundColor Gray
Read-Host "Appuie sur Entree pour fermer"
