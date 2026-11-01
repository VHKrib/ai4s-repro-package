<#
.SYNOPSIS
    Create the two Python environments a4s-repro needs and install the pinned deps.

.DESCRIPTION
    envs/py3119-science  Python 3.11.9 + requirements/science.txt  (RDKit, Meeko, Gemmi)
    envs/py3119-stage3   Python 3.11.9, standard library only      (requirements/stage3.txt)

    Nothing is installed into the system interpreter and nothing outside this
    package is touched. Re-running is safe: existing environments are reused and
    only missing packages are installed.

.PARAMETER PythonExe
    A Python 3.11 interpreter used to create the venvs. Defaults to `py -3.11`,
    then `python` on PATH. Must be CPython 3.11.x (3.11.9 is what the reference
    results were produced with).

.PARAMETER Force
    Delete and recreate the environments.

.PARAMETER IncludeBridge
    Also create envs/py3119-bridge with `requests`, for scripts/sciencepal_bridge.py.
    Optional: the local 02/03 pipeline never needs it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File setup_envs.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File setup_envs.ps1 -PythonExe C:\Python311\python.exe -Force
#>
[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$PythonExe = "",
    [switch]$Force,
    [switch]$IncludeBridge,
    [switch]$UpgradePip
)

$ErrorActionPreference = "Stop"
# $PSScriptRoot is not reliably populated in a param-block default (Windows
# PowerShell 5.1), so resolve it here where it is always defined.
if (-not $Root) { $Root = $PSScriptRoot }
if (-not $Root) { $Root = (Get-Location).Path }
$Root = (Resolve-Path -LiteralPath $Root).Path
Set-Location $Root

function Write-Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Write-Ok($text)   { Write-Host "[ok]   $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "[warn] $text" -ForegroundColor Yellow }

# --------------------------------------------------------------------------
# 0. This host (and some corporate images) point the TLS bundle env vars at a
#    file that does not exist; pip then dies with
#    "Could not find a suitable TLS CA certificate bundle". Clearing a broken
#    value is safe: pip falls back to its bundled certifi store.
# --------------------------------------------------------------------------
foreach ($var in @("CURL_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")) {
    $value = [Environment]::GetEnvironmentVariable($var, "Process")
    if ($value -and -not (Test-Path $value)) {
        Write-Warn "$var points at a missing file ($value); unsetting it for this session."
        Remove-Item "Env:$var" -ErrorAction SilentlyContinue
    }
}

# --------------------------------------------------------------------------
# 1. Find a CPython 3.11 interpreter
# --------------------------------------------------------------------------
function Resolve-Python {
    param([string]$Explicit)
    if ($Explicit) {
        if (-not (Test-Path $Explicit)) { throw "PythonExe not found: $Explicit" }
        return $Explicit
    }
    $tried = @()
    $candidates = @()
    if ($env:A4S_PYTHON311) { $candidates += $env:A4S_PYTHON311 }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try { $candidates += (& py -3.11 -c "import sys; print(sys.executable)").Trim() } catch { }
    }
    foreach ($name in @("python3.11", "python")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { $candidates += $cmd.Source }
    }
    $candidates += @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        "C:\Python311\python.exe",
        "C:\Program Files\Python311\python.exe"
    )
    foreach ($candidate in $candidates) {
        if (-not $candidate) { continue }
        if (-not (Test-Path $candidate)) { $tried += "$candidate (not found)"; continue }
        $version = (& $candidate -c "import sys; print('%d.%d.%d' % sys.version_info[:3])").Trim()
        if ($version -like "3.11.*") { return $candidate }
        $tried += "$candidate (CPython $version)"
    }
    throw ("No CPython 3.11 found. Install Python 3.11.9, then either put it on PATH, " +
           "pass -PythonExe <path>, or set A4S_PYTHON311=<path>.`nTried:`n  " +
           ($tried -join "`n  "))
}

$BasePython = Resolve-Python $PythonExe
$BaseVersion = (& $BasePython -c "import sys; print('%d.%d.%d' % sys.version_info[:3])").Trim()
Write-Host "base interpreter : $BasePython (CPython $BaseVersion)"
if ($BaseVersion -ne "3.11.9") {
    Write-Warn "reference results were produced with CPython 3.11.9; you have $BaseVersion."
    Write-Warn "The pipeline should still run, but wheel builds / numeric output may differ."
}

New-Item -ItemType Directory -Force -Path "envs" | Out-Null
$envs = @(
    @{ Name = "py3119-science"; Requirements = "requirements/science.txt"; StdlibOnly = $false },
    @{ Name = "py3119-stage3";  Requirements = "requirements/stage3.txt";  StdlibOnly = $true }
)
if ($IncludeBridge) {
    $envs += @{ Name = "py3119-bridge"; Requirements = "requirements/bridge.txt"; StdlibOnly = $false }
}

foreach ($spec in $envs) {
    $dir = Join-Path "envs" $spec.Name
    if ($Force -and (Test-Path $dir)) {
        Write-Step "removing $dir (-Force)"
        Remove-Item -Recurse -Force $dir
    }
    if (-not (Test-Path $dir)) {
        Write-Step "creating $dir"
        & $BasePython -m venv $dir
        if ($LASTEXITCODE -ne 0) { throw "python -m venv failed for $dir (exit $LASTEXITCODE)" }
    } else {
        Write-Step "reusing $dir"
    }

    $py = Join-Path $dir "Scripts\python.exe"
    if (-not (Test-Path $py)) { throw "venv looks broken, no interpreter at $py" }
    $version = (& $py -c "import sys; print('%d.%d.%d' % sys.version_info[:3])").Trim()
    Write-Ok "$($spec.Name): CPython $version"

    if ($UpgradePip) {
        & $py -m pip install --disable-pip-version-check --quiet --upgrade pip
        if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed in $dir" }
    }

    if ($spec.StdlibOnly) {
        Write-Ok "$($spec.Name): no third-party packages required (stdlib only)"
        continue
    }

    Write-Host "installing $($spec.Requirements) ..."
    & $py -m pip install --disable-pip-version-check --no-input -r $spec.Requirements
    if ($LASTEXITCODE -ne 0) { throw "pip install -r $($spec.Requirements) failed (exit $LASTEXITCODE)" }
}

# --------------------------------------------------------------------------
# 2. Post-install verification: the console scripts the pipeline calls must exist
# --------------------------------------------------------------------------
Write-Step "verifying environments"
$sciencePy = Join-Path -Path $Root -ChildPath "envs\py3119-science\Scripts\python.exe"
$stage3Py = Join-Path -Path $Root -ChildPath "envs\py3119-stage3\Scripts\python.exe"
Write-Host "science python : $sciencePy"
Write-Host "stage3  python : $stage3Py"
foreach ($exe in @("mk_prepare_receptor.exe", "mk_prepare_ligand.exe", "mk_export.exe")) {
    $exePath = Join-Path -Path $Root -ChildPath ("envs\py3119-science\Scripts\" + $exe)
    if (-not (Test-Path -LiteralPath $exePath)) { throw "meeko console script missing: $exePath" }
    Write-Ok "found $exe"
}
& $sciencePy -c "import rdkit, gemmi, meeko; print('rdkit', rdkit.__version__, '| gemmi', gemmi.__version__, '| meeko', meeko.__version__)"
if ($LASTEXITCODE -ne 0) { throw "science environment import check failed" }
& $stage3Py -c "import json, csv, statistics; print('stage3 stdlib import check ok')"
if ($LASTEXITCODE -ne 0) { throw "stage3 environment import check failed" }
$vinaPath = Join-Path -Path $Root -ChildPath "tools\vina_1.2.5_win.exe"
if (-not (Test-Path -LiteralPath $vinaPath)) { throw "missing binary: $vinaPath" }
Write-Ok "vina binary present"

Write-Host "`nAll environments ready. Next:" -ForegroundColor Green
Write-Host "  python tests\smoke_test.py"
Write-Host "  python scripts\run_pipeline.py"
