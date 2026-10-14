param(
    [string]$PythonExe = "python",
    [switch]$SkipDownloads
)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Source = Get-Content -LiteralPath (Join-Path $ProjectRoot "configs\source_versions.json") -Raw | ConvertFrom-Json
$AreCommit = $Source.source_commit
$MantaCommit = "3da4087"
$AreRoot = Join-Path $ProjectRoot "work\facebookresearch-meta-agents-research-environments-a9fd2ed"
$MantaRoot = Join-Path $ProjectRoot "work\mao-code-MANTA-3da4087"
$DataDir = Join-Path $ProjectRoot "work\data"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$env:UV_CACHE_DIR = Join-Path $ProjectRoot "uv-cache"
$env:HF_HOME = Join-Path $ProjectRoot "work\hf_cache"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required for installation; install uv before running bootstrap.ps1"
}

$Version = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $Version -ne "3.12") { throw "Python 3.12 required; found $Version" }

function Ensure-PinnedRepo([string]$Url, [string]$Path, [string]$Commit) {
    if (-not (Test-Path -LiteralPath $Path)) {
        git clone $Url $Path
        if ($LASTEXITCODE -ne 0) { throw "git clone failed: $Url" }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Path ".git"))) {
        throw "Existing source lacks Git metadata: $Path. Use a clean checkout for source verification."
    }
    git -C $Path checkout --detach $Commit
    if ($LASTEXITCODE -ne 0) { throw "Cannot check out pinned commit: $Commit" }
    $Actual = (git -C $Path rev-parse HEAD).Trim()
    if (-not $Actual.StartsWith($Commit)) { throw "Source revision mismatch: $Path" }
}

if (-not $SkipDownloads) {
    Ensure-PinnedRepo "https://github.com/facebookresearch/meta-agents-research-environments.git" $AreRoot $AreCommit
    Ensure-PinnedRepo "https://github.com/mao-code/MANTA.git" $MantaRoot $MantaCommit
    & $PythonExe (Join-Path $ProjectRoot "security_adaptive_mas\dataset_store.py") fetch
    if ($LASTEXITCODE -ne 0) { throw "Gaia2 release download or checksum verification failed" }
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    & $PythonExe -m venv --without-pip (Join-Path $ProjectRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed" }
}
uv pip install --python $VenvPython -r (Join-Path $ProjectRoot "requirements-v2.txt")
if ($LASTEXITCODE -ne 0) { throw "Project dependency installation failed" }
uv pip install --python $VenvPython -r (Join-Path $AreRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "ARE dependency installation failed" }
if ($SkipDownloads) {
    & $VenvPython -c "from security_adaptive_mas.config import load_config; cfg=load_config(); [cfg.select(s) for s in cfg.scenario_ids]; print('Five pinned scenario checksums OK')"
    if ($LASTEXITCODE -ne 0) { throw "Existing fixed scenario verification failed" }
} else {
    & $VenvPython -m security_adaptive_mas.bootstrap_data --data-dir $DataDir
    if ($LASTEXITCODE -ne 0) { throw "Fixed scenario restoration failed" }
}
Push-Location $ProjectRoot
try {
    & $VenvPython -m pytest -q -p no:cacheprovider tests
    if ($LASTEXITCODE -ne 0) { throw "Offline tests failed" }
} finally { Pop-Location }
