# Start Outlawz from a source checkout (Windows PowerShell).
#
#   .\scripts\run.ps1                 # set up once, then start and open the browser
#   .\scripts\run.ps1 --port 9000     # any `outlawz start` option works
#
# If PowerShell refuses to run scripts, allow it for this window only:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Venv = Join-Path $Root "backend\.venv"
$PyInVenv = Join-Path $Venv "Scripts\python.exe"

function Find-Python {
    # Native commands that print to stderr (e.g. "py -3.13" when 3.13 isn't installed) must not stop the script.
    $ErrorActionPreference = "Continue"
    foreach ($candidate in @(@("py", "-3.13"), @("py", "-3.12"), @("py", "-3.11"), @("python"), @("python3"))) {
        $exe = $candidate[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $rest = @($candidate | Select-Object -Skip 1)
        & $exe @rest -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { return , $candidate }
    }
    return $null
}

if (-not (Test-Path $PyInVenv)) {
    $python = Find-Python
    if ($null -eq $python) {
        Write-Error "Outlawz needs Python 3.11 or newer. Get it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and run this again."
    }
    Write-Host "Setting up Outlawz (first run only, takes a minute)..."
    $exe = $python[0]
    $rest = @($python | Select-Object -Skip 1)
    & $exe @rest -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "Couldn't create the Python environment." }
}

$Stamp = Join-Path $Venv ".outlawz-installed"
$Pyproject = Join-Path $Root "backend\pyproject.toml"
if (-not (Test-Path $Stamp) -or (Get-Item $Pyproject).LastWriteTime -gt (Get-Item $Stamp).LastWriteTime) {
    & $PyInVenv -m pip install --quiet --disable-pip-version-check -e (Join-Path $Root "backend")
    if ($LASTEXITCODE -ne 0) { throw "Installing Outlawz failed." }
    New-Item -ItemType File -Force -Path $Stamp | Out-Null
}

if (-not (Test-Path (Join-Path $Root "backend\outlawz\web\index.html"))) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Write-Host "Building the web app (first run only)..."
        & $PyInVenv (Join-Path $Root "scripts\build_web.py")
        if ($LASTEXITCODE -ne 0) { throw "Building the web app failed." }
    } else {
        Write-Warning "Node.js isn't installed, so only the API will be available. Install Node.js 20 or newer and run this again, or install a release with: pipx install outlawz"
    }
}

& (Join-Path $Venv "Scripts\outlawz.exe") start @args
exit $LASTEXITCODE
