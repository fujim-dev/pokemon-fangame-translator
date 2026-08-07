#requires -Version 5.1
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$ReleaseDir = Join-Path $Root "release"
$Out = Join-Path $ReleaseDir "Pokemon_Fangame_Translator_Source_v1.0.2.zip"
$ArchivePrefix = "Pokemon_Fangame_Translator_Source_v1.0.2/"

$GitCommand = Get-Command git.exe -ErrorAction SilentlyContinue
if (-not $GitCommand) {
    $GitCommand = Get-Command git -ErrorAction SilentlyContinue
}
if (-not $GitCommand -or -not (Test-Path (Join-Path $Root ".git"))) {
    throw "Git et le dépôt .git sont nécessaires pour créer une archive source sûre."
}
$Git = $GitCommand.Source

# Une archive publique ne doit jamais récupérer des fichiers ignorés, des
# échantillons privés ou des changements locaux non relus. Elle représente
# donc uniquement un commit propre et reproductible.
$Status = @(& $Git -C $Root status --porcelain --untracked-files=all)
if ($LASTEXITCODE -ne 0) {
    throw "Impossible de vérifier l'état Git du dépôt."
}
if ($Status.Count -gt 0) {
    $Preview = ($Status | Select-Object -First 8) -join "`n"
    throw "Le dépôt contient des changements non enregistrés. Crée d'abord un commit, puis relance l'archive.`n`n$Preview"
}

$TrackedFiles = @(& $Git -C $Root ls-tree -r --name-only HEAD)
if ($LASTEXITCODE -ne 0 -or $TrackedFiles.Count -eq 0) {
    throw "Aucun fichier suivi n'a pu être lu dans le commit courant."
}

$ForbiddenExtensions = @(
    ".rxdata", ".dat", ".fpk", ".rgssad", ".rgss2a", ".rgss3a",
    ".sav", ".save", ".zip", ".7z", ".rar"
)
$ForbiddenDirectoryNames = @(
    "data", "pbs", "audio", "graphics", "travail_echantillon",
    "sortie_extraction", "rapports", "sauvegardes"
)
$AllowedCsv = @(
    "glossaire_v1.0.2.csv",
    "corrections_apprises_v1.0.2.csv"
)

$Violations = New-Object 'System.Collections.Generic.List[string]'
foreach ($TrackedFile in $TrackedFiles) {
    $Normalized = ([string]$TrackedFile).Replace("\", "/")
    $Extension = [System.IO.Path]::GetExtension($Normalized).ToLowerInvariant()
    $Segments = @($Normalized.Split("/"))

    if ($ForbiddenExtensions -contains $Extension) {
        $Violations.Add("Extension interdite : $Normalized")
        continue
    }
    if ($Extension -eq ".csv" -and $AllowedCsv -notcontains $Normalized) {
        $Violations.Add("CSV non autorisé : $Normalized")
        continue
    }
    foreach ($Segment in $Segments) {
        $LowerSegment = $Segment.ToLowerInvariant()
        if (
            $ForbiddenDirectoryNames -contains $LowerSegment -or
            $LowerSegment.StartsWith("sauvegardes")
        ) {
            $Violations.Add("Dossier de données interdit : $Normalized")
            break
        }
    }
}

if ($Violations.Count -gt 0) {
    throw "L'archive source est bloquée car le commit contient des données interdites :`n`n$($Violations -join "`n")"
}

New-Item -Path $ReleaseDir -ItemType Directory -Force | Out-Null
if (Test-Path -LiteralPath $Out) {
    Remove-Item -LiteralPath $Out -Force
}

& $Git -C $Root archive --format=zip "--prefix=$ArchivePrefix" "--output=$Out" HEAD
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Out)) {
    throw "Git n'a pas pu créer l'archive source."
}

Write-Host "Archive source sûre créée depuis le commit courant : $Out" -ForegroundColor Green
