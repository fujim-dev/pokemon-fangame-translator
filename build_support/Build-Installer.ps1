#requires -Version 5.1
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$DownloadsDir = Join-Path $Root ".downloads"
$PackagesDir = Join-Path $Root ".build_packages"
$BuildLog = Join-Path $Root "BUILD_WINDOWS.log"
$ReleaseDir = Join-Path $Root "release"

New-Item -ItemType Directory -Path $DownloadsDir -Force | Out-Null
New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor DarkCyan
    Write-Host $Text -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor DarkCyan
}

function Write-Log([string]$Text) {
    $Line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Text"
    Add-Content -Path $BuildLog -Value $Line -Encoding UTF8
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory=$true)][string]$FilePath,
        [Parameter(Mandatory=$false)][string[]]$ArgumentList = @(),
        [Parameter(Mandatory=$false)][hashtable]$Environment = @{}
    )

    $DisplayArguments = @($ArgumentList | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\\"') + '"' } else { $_ }
    })
    Write-Log "Commande : $FilePath $($DisplayArguments -join ' ')"

    $Previous = @{}
    foreach ($Name in $Environment.Keys) {
        $Previous[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
        [Environment]::SetEnvironmentVariable($Name, [string]$Environment[$Name], "Process")
    }

    try {
        & $FilePath @ArgumentList
        $ExitCode = $LASTEXITCODE
    }
    finally {
        foreach ($Name in $Environment.Keys) {
            [Environment]::SetEnvironmentVariable($Name, $Previous[$Name], "Process")
        }
    }

    if ($ExitCode -ne 0) {
        throw "La commande a echoue (code $ExitCode) : $FilePath"
    }
}

function Invoke-Download {
    param(
        [Parameter(Mandatory=$true)][string]$Url,
        [Parameter(Mandatory=$true)][string]$Destination
    )

    Write-Host "Telechargement officiel :" -ForegroundColor Gray
    Write-Host $Url -ForegroundColor DarkGray
    Write-Log "Telechargement : $Url"

    $Temp = "$Destination.part"
    Remove-Item $Temp -Force -ErrorAction SilentlyContinue

    try {
        Invoke-WebRequest -Uri $Url -OutFile $Temp -UseBasicParsing
    }
    catch {
        Write-Host "Nouvel essai avec le service de transfert Windows..." -ForegroundColor Yellow
        Import-Module BitsTransfer -ErrorAction Stop
        Start-BitsTransfer -Source $Url -Destination $Temp
    }

    if (-not (Test-Path $Temp)) {
        throw "Le telechargement n'a produit aucun fichier."
    }
    Move-Item $Temp $Destination -Force
}

function Get-PythonProbe([string]$Candidate) {
    if (-not $Candidate -or -not (Test-Path $Candidate)) { return $null }

    $ProbeScript = Join-Path $Root "build_support\probe_python.py"
    if (-not (Test-Path $ProbeScript)) {
        Write-Log "Sonde Python absente : $ProbeScript"
        return $null
    }

    try {
        # FIX6 : utiliser un vrai fichier .py au lieu d'un long argument -c.
        # Cela évite les erreurs de découpage/quoting de Windows PowerShell 5.1.
        $Output = @(& $Candidate $ProbeScript 2>&1)
        $ExitCode = $LASTEXITCODE
        $Text = ($Output | ForEach-Object { [string]$_ }) -join "`n"
        Write-Log "Sonde Python [$Candidate] code=$ExitCode sortie=$Text"

        $JsonLine = $Output |
            ForEach-Object { [string]$_ } |
            Where-Object { $_.TrimStart().StartsWith("{") } |
            Select-Object -Last 1

        if (-not $JsonLine) {
            return [PSCustomObject]@{
                ok = $false
                executable = $Candidate
                version = "inconnue"
                major = 0
                minor = 0
                bits = 0
                tkinter = $false
                tk_version = $null
                error = "La sonde Python n'a renvoye aucune ligne JSON."
            }
        }

        return ($JsonLine | ConvertFrom-Json)
    }
    catch {
        Write-Log "Erreur de sonde Python [$Candidate] : $($_.Exception.Message)"
        return [PSCustomObject]@{
            ok = $false
            executable = $Candidate
            version = "inconnue"
            major = 0
            minor = 0
            bits = 0
            tkinter = $false
            tk_version = $null
            error = $_.Exception.Message
        }
    }
}

function Get-PythonDescription([string]$Candidate) {
    $Probe = Get-PythonProbe $Candidate
    if ($Probe -and $Probe.ok) {
        return "$($Probe.executable)|$($Probe.version)|$($Probe.bits)|$($Probe.tk_version)"
    }
    return $null
}

function Get-PythonRejectionReason([string]$Candidate) {
    $Probe = Get-PythonProbe $Candidate
    if (-not $Probe) {
        return "Le fichier n'a pas pu etre lance."
    }

    if ($Probe.major -ne 3 -or $Probe.minor -lt 10 -or $Probe.minor -gt 13) {
        return "Version detectee : $($Probe.version). Le kit accepte Python 3.10 a 3.13."
    }
    if ($Probe.bits -ne 64) {
        return "Python $($Probe.version) est en $($Probe.bits) bits. Une version 64 bits est requise."
    }
    if (-not $Probe.tkinter) {
        $Details = [string]$Probe.error
        if ($Details.Length -gt 450) { $Details = $Details.Substring(0, 450) + "..." }
        return "Python $($Probe.version) est compatible, mais Tkinter n'a pas pu etre charge.`n`n$Details"
    }
    if (-not $Probe.ok) {
        return "Python $($Probe.version) a ete lance, mais la verification n'a pas abouti."
    }
    return "Raison inconnue. Consulte BUILD_WINDOWS.log."
}

function Add-PythonCandidate {
    param(
        [System.Collections.Generic.List[string]]$List,
        [string]$Candidate
    )
    if (-not $Candidate) { return }
    try {
        $Expanded = [Environment]::ExpandEnvironmentVariables($Candidate.Trim('" '))
        if ($Expanded -and -not $List.Contains($Expanded)) { $List.Add($Expanded) }
    }
    catch {}
}

function Get-PythonCandidates {
    $Candidates = New-Object 'System.Collections.Generic.List[string]'

    foreach ($Version in @("3.11", "3.12", "3.13", "3.10")) {
        foreach ($LauncherName in @("py.exe", "py")) {
            $Launcher = Get-Command $LauncherName -ErrorAction SilentlyContinue
            if ($Launcher) {
                try {
                    $Detected = & $Launcher.Source "-$Version" -c "import sys; print(sys.executable)" 2>$null
                    if ($LASTEXITCODE -eq 0 -and $Detected) {
                        Add-PythonCandidate $Candidates (($Detected | Select-Object -Last 1).Trim())
                    }
                }
                catch {}
            }
        }
    }

    $RegistryBases = @(
        "HKCU:\Software\Python\PythonCore",
        "HKLM:\Software\Python\PythonCore",
        "HKLM:\Software\WOW6432Node\Python\PythonCore"
    )
    foreach ($Base in $RegistryBases) {
        if (-not (Test-Path $Base)) { continue }
        try {
            foreach ($VersionKey in Get-ChildItem $Base -ErrorAction SilentlyContinue) {
                $InstallKey = Join-Path $VersionKey.PSPath "InstallPath"
                if (-not (Test-Path $InstallKey)) { continue }
                $Props = Get-ItemProperty $InstallKey -ErrorAction SilentlyContinue
                Add-PythonCandidate $Candidates $Props.ExecutablePath
                if ($Props.'(default)') {
                    Add-PythonCandidate $Candidates (Join-Path $Props.'(default)' "python.exe")
                }
            }
        }
        catch {}
    }

    foreach ($CommandName in @("python.exe", "python3.exe", "python")) {
        try {
            $Command = Get-Command $CommandName -ErrorAction SilentlyContinue
            if ($Command -and $Command.Source -notmatch "\\WindowsApps\\") {
                Add-PythonCandidate $Candidates $Command.Source
            }
        }
        catch {}
    }

    try {
        foreach ($Result in (& where.exe python.exe 2>$null)) {
            if ($Result -notmatch "\\WindowsApps\\") { Add-PythonCandidate $Candidates $Result }
        }
    }
    catch {}

    foreach ($ExplicitCandidate in @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python310\python.exe")
    )) {
        Add-PythonCandidate $Candidates $ExplicitCandidate
    }

    $CommonRoots = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python"),
        (Join-Path $env:LOCALAPPDATA "Python"),
        $env:ProgramFiles,
        ${env:ProgramFiles(x86)},
        "C:\Python310",
        "C:\Python311",
        "C:\Python312",
        "C:\Python313"
    )
    foreach ($SearchRoot in $CommonRoots) {
        if (-not $SearchRoot -or -not (Test-Path $SearchRoot)) { continue }
        if ((Get-Item $SearchRoot).PSIsContainer) {
            try {
                Get-ChildItem -Path $SearchRoot -Filter "python.exe" -Recurse -Depth 4 -ErrorAction SilentlyContinue |
                    ForEach-Object { Add-PythonCandidate $Candidates $_.FullName }
            }
            catch {
                # Windows PowerShell 5.1 ne connait pas toujours -Depth.
                try {
                    Get-ChildItem -Path $SearchRoot -Filter "python.exe" -Recurse -ErrorAction SilentlyContinue |
                        ForEach-Object { Add-PythonCandidate $Candidates $_.FullName }
                }
                catch {}
            }
        }
        else {
            Add-PythonCandidate $Candidates $SearchRoot
        }
    }

    return $Candidates
}

function Find-CompatiblePython {
    foreach ($Candidate in (Get-PythonCandidates)) {
        $Description = Get-PythonDescription $Candidate
        if ($Description) {
            $Parts = $Description.Split('|')
            Write-Host "Python compatible detecte : $($Parts[0]) — version $($Parts[1])" -ForegroundColor Green
            Write-Log "Python compatible : $Description"
            return $Parts[0]
        }
    }
    return $null
}

function Select-PythonManually {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $Dialog = New-Object System.Windows.Forms.OpenFileDialog
        $Dialog.Title = "Selectionne le fichier python.exe deja installe"
        $Dialog.Filter = "Python (python.exe)|python.exe|Executables (*.exe)|*.exe"
        $Dialog.CheckFileExists = $true
        $Dialog.Multiselect = $false
        if ($Dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            $Description = Get-PythonDescription $Dialog.FileName
            if ($Description) { return $Dialog.FileName }
            $Reason = Get-PythonRejectionReason $Dialog.FileName
            Write-Log "Python choisi mais refuse : $($Dialog.FileName) — $Reason"
            [System.Windows.Forms.MessageBox]::Show(
                $Reason,
                "Verification de Python",
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Warning
            ) | Out-Null
        }
    }
    catch {
        Write-Log "Selection manuelle impossible : $($_.Exception.Message)"
    }
    return $null
}

function Ensure-CompatiblePython {
    $Python = Find-CompatiblePython
    if ($Python) { return $Python }

    Write-Step "PYTHON INTROUVABLE — CHOIX MANUEL"
    Write-Host "Le kit n'a pas trouve automatiquement ton Python deja installe." -ForegroundColor Yellow
    Write-Host "Une fenetre va te permettre de selectionner directement python.exe." -ForegroundColor Gray
    $Python = Select-PythonManually
    if ($Python) {
        Write-Host "Python choisi : $Python" -ForegroundColor Green
        Write-Log "Python choisi manuellement : $Python"
        return $Python
    }

    Write-Step "INSTALLATION INTERACTIVE DE PYTHON"
    Write-Host "L'installation silencieuse ne fonctionne pas correctement sur ce PC." -ForegroundColor Yellow
    Write-Host "L'assistant officiel Python va donc s'ouvrir normalement." -ForegroundColor Gray
    Write-Host "Dans la premiere fenetre :" -ForegroundColor White
    Write-Host "  1. coche Add python.exe to PATH ;" -ForegroundColor White
    Write-Host "  2. clique Install Now ;" -ForegroundColor White
    Write-Host "  3. attends la fin puis ferme l'assistant." -ForegroundColor White
    Read-Host "Appuie sur Entree pour ouvrir l'installateur officiel"

    $Url = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
    $Installer = Join-Path $DownloadsDir "python-3.11.9-amd64.exe"
    if (-not (Test-Path $Installer)) { Invoke-Download $Url $Installer }

    $Signature = Get-AuthenticodeSignature $Installer
    if ($Signature.Status -ne "Valid") {
        throw "La signature numerique de l'installateur Python n'est pas valide : $($Signature.Status)"
    }

    $Process = Start-Process -FilePath $Installer -Wait -PassThru
    Write-Log "Installateur Python interactif termine avec le code $($Process.ExitCode)"
    Start-Sleep -Seconds 2

    $Python = Find-CompatiblePython
    if ($Python) { return $Python }

    Write-Host "Python reste introuvable automatiquement." -ForegroundColor Yellow
    Write-Host "Selectionne maintenant le python.exe que tu viens d'installer." -ForegroundColor Gray
    $Python = Select-PythonManually
    if ($Python) { return $Python }

    throw "Aucun Python compatible n'a ete selectionne. Relance le build apres avoir installe Python 3.11 64 bits."
}

function Ensure-Pip([string]$Python) {
    & $Python -m pip --version 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { return }

    Write-Host "Installation de pip dans ce Python..." -ForegroundColor Yellow
    & $Python -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) {
        throw "pip est absent et ensurepip n'a pas pu l'installer."
    }
}

function Find-Iscc {
    $Candidates = New-Object 'System.Collections.Generic.List[string]'
    foreach ($Candidate in @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 7\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 7\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 7\ISCC.exe")
    )) {
        if ($Candidate -and (Test-Path $Candidate)) { $Candidates.Add($Candidate) }
    }
    try {
        $Command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($Command) { $Candidates.Add($Command.Source) }
    }
    catch {}

    foreach ($Candidate in $Candidates) {
        if (Test-Path $Candidate) { return $Candidate }
    }
    return $null
}

function Select-IsccManually {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $Dialog = New-Object System.Windows.Forms.OpenFileDialog
        $Dialog.Title = "Selectionne ISCC.exe (Inno Setup)"
        $Dialog.Filter = "Compilateur Inno Setup (ISCC.exe)|ISCC.exe|Executables (*.exe)|*.exe"
        $Dialog.CheckFileExists = $true
        if ($Dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            return $Dialog.FileName
        }
    }
    catch {}
    return $null
}

function Ensure-Iscc {
    $Iscc = Find-Iscc
    if ($Iscc) {
        Write-Host "Inno Setup detecte : $Iscc" -ForegroundColor Green
        return $Iscc
    }

    Write-Step "INSTALLATION D'INNO SETUP"
    Write-Host "La page officielle d'Inno Setup va s'ouvrir." -ForegroundColor Gray
    Write-Host "Telecharge Inno Setup 6, installe-le avec les options par defaut, puis reviens ici." -ForegroundColor White
    Start-Process "https://jrsoftware.org/isdl.php"
    Read-Host "Appuie sur Entree APRES avoir installe Inno Setup"

    $Iscc = Find-Iscc
    if ($Iscc) { return $Iscc }

    Write-Host "Selectionne manuellement ISCC.exe." -ForegroundColor Yellow
    $Iscc = Select-IsccManually
    if ($Iscc) { return $Iscc }

    throw "ISCC.exe reste introuvable. Installe Inno Setup puis relance le build."
}

try {
    Clear-Host
    Set-Content -Path $BuildLog -Value "Pokemon Fangame Translator - journal du build v1.0.2" -Encoding UTF8

    Write-Host "POKEMON FANGAME TRANSLATOR - INSTALLATEUR WINDOWS" -ForegroundColor Magenta
    Write-Host "v1.0.2 : build public avec licences et empreinte SHA-256." -ForegroundColor DarkGray

    $Python = Ensure-CompatiblePython
    Ensure-Pip $Python

    Write-Step "INSTALLATION LOCALE DES OUTILS DE BUILD"
    if (Test-Path $PackagesDir) { Remove-Item $PackagesDir -Recurse -Force }
    New-Item -ItemType Directory -Path $PackagesDir -Force | Out-Null

    Invoke-Checked -FilePath $Python -ArgumentList @(
        "-m", "pip", "install",
        "--disable-pip-version-check", "--retries", "5", "--timeout", "180",
        "--upgrade", "--target", $PackagesDir,
        "-r", (Join-Path $Root "build_support\requirements-build.txt")
    )

    $BuildEnvironment = @{
        "PYTHONPATH" = "$PackagesDir;$Root"
        "PATH" = "$PackagesDir;$env:PATH"
    }

    Invoke-Checked -FilePath $Python -ArgumentList @(
        (Join-Path $Root "build_support\verify_sources.py")
    ) -Environment $BuildEnvironment

    Invoke-Checked -FilePath $Python -ArgumentList @(
        (Join-Path $Root "build_support\generate_third_party_notices.py")
    ) -Environment $BuildEnvironment

    foreach ($FolderName in @("build", "dist")) {
        $Folder = Join-Path $Root $FolderName
        if (Test-Path $Folder) { Remove-Item $Folder -Recurse -Force }
    }

    Write-Step "CREATION DE L'APPLICATION WINDOWS"
    Invoke-Checked -FilePath $Python -ArgumentList @(
        "-m", "PyInstaller", "--noconfirm", "--clean",
        (Join-Path $Root "build_support\PokemonFangameTranslator.spec")
    ) -Environment $BuildEnvironment

    $BuiltExe = Join-Path $Root "dist\PokemonFangameTranslator\PokemonFangameTranslator.exe"
    if (-not (Test-Path $BuiltExe)) {
        throw "PyInstaller s'est termine, mais l'application Windows est introuvable."
    }

    $Iscc = Ensure-Iscc

    Write-Step "CREATION DU SETUP.EXE"
    Invoke-Checked -FilePath $Iscc -ArgumentList @(
        (Join-Path $Root "build_support\installer\PokemonFangameTranslator.iss")
    )

    $Setup = Join-Path $ReleaseDir "Pokemon_Fangame_Translator_Setup_v1.0.2.exe"
    if (-not (Test-Path $Setup)) {
        $Setup = Get-ChildItem $ReleaseDir -Filter "Pokemon_Fangame_Translator_Setup_*.exe" |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $Setup -or -not (Test-Path $Setup)) {
        throw "Aucun Setup.exe n'a ete trouve dans le dossier release."
    }

    $Hash = (Get-FileHash $Setup -Algorithm SHA256).Hash.ToLowerInvariant()
    $HashFile = Join-Path $ReleaseDir "SHA256.txt"
    Set-Content -Path $HashFile -Value "$Hash  $([System.IO.Path]::GetFileName($Setup))" -Encoding ASCII
    Copy-Item (Join-Path $Root "RELEASE_NOTES_V1.0.2.md") $ReleaseDir -Force
    Copy-Item (Join-Path $Root "LICENSE") $ReleaseDir -Force

    Write-Step "TERMINE"
    Write-Host "Installateur cree avec succes :" -ForegroundColor Green
    Write-Host $Setup -ForegroundColor White
    Write-Host ""
    Write-Host "Teste ce Setup.exe avant toute publication." -ForegroundColor Yellow
    Write-Host "Journal : $BuildLog" -ForegroundColor DarkGray
    Start-Process explorer.exe -ArgumentList $ReleaseDir
    Read-Host "Appuie sur Entree pour fermer"
    exit 0
}
catch {
    Write-Log "ERREUR : $($_.Exception.Message)"
    Write-Host ""
    Write-Host "ECHEC DU BUILD" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    Write-Host "Journal : $BuildLog" -ForegroundColor Yellow
    Write-Host "Prends une capture et joins BUILD_WINDOWS.log." -ForegroundColor Yellow
    Read-Host "Appuie sur Entree pour fermer"
    exit 1
}
