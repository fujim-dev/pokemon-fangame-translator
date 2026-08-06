$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Out = Join-Path $Root "release\Pokemon_Fangame_Translator_Source_v1.0.2.zip"
New-Item (Split-Path $Out -Parent) -ItemType Directory -Force | Out-Null
if (Test-Path $Out) { Remove-Item $Out -Force }
$Exclude = @(".build_packages", ".downloads", "build", "dist", "release", "__pycache__")
$Staging = Join-Path $env:TEMP "PFT_Source_v1.0.2"
if (Test-Path $Staging) { Remove-Item $Staging -Recurse -Force }
New-Item $Staging -ItemType Directory | Out-Null
Get-ChildItem $Root -Force | Where-Object { $Exclude -notcontains $_.Name } | ForEach-Object {
    Copy-Item $_.FullName $Staging -Recurse -Force
}
Compress-Archive -Path (Join-Path $Staging "*") -DestinationPath $Out -CompressionLevel Optimal
Remove-Item $Staging -Recurse -Force
Write-Host "Archive source créée : $Out" -ForegroundColor Green
