[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$frontendDir = Join-Path $repoRoot "src\frontend"
$defaultDistDir = Join-Path $repoRoot "dist"
$distExe = Join-Path $repoRoot "dist\Reviewer.exe"
$releaseRoot = Join-Path $repoRoot "release-dist"

function Stop-ReviewerProcesses {
    $running = Get-Process -Name "Reviewer" -ErrorAction SilentlyContinue
    if (-not $running) {
        return
    }

    Write-Host "==> Reviewer.exe is running; stopping existing process(es)"
    foreach ($process in $running) {
        Stop-Process -Id $process.Id -Force -ErrorAction Stop
    }
    Start-Sleep -Milliseconds 800
}

function Resolve-PackageOutput {
    if (-not (Test-Path $distExe)) {
        if (-not (Test-Path $defaultDistDir)) {
            New-Item -ItemType Directory -Path $defaultDistDir | Out-Null
        }
        return @{
            DistPath = $defaultDistDir
            OutputExe = $distExe
        }
    }

    $probePath = "$distExe.overwrite-check"
    try {
        Move-Item -Path $distExe -Destination $probePath -Force
        Move-Item -Path $probePath -Destination $distExe -Force
        return @{
            DistPath = $defaultDistDir
            OutputExe = $distExe
        }
    }
    catch {
        $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $fallbackDir = Join-Path $releaseRoot $timestamp
        New-Item -ItemType Directory -Path $fallbackDir -Force | Out-Null
        Write-Host "==> dist\\Reviewer.exe cannot be overwritten safely; packaging to $fallbackDir"
        return @{
            DistPath = $fallbackDir
            OutputExe = (Join-Path $fallbackDir "Reviewer.exe")
        }
    }
    finally {
        if (Test-Path $probePath) {
            Move-Item -Path $probePath -Destination $distExe -Force
        }
    }
}

Stop-ReviewerProcesses
$packageOutput = Resolve-PackageOutput

Write-Host "==> Sync Python dependencies"
Push-Location $repoRoot
uv sync --extra dev
Pop-Location

Write-Host "==> Build frontend dist"
Push-Location $frontendDir
npm install
npm run build
Pop-Location

Write-Host "==> Package Reviewer.exe"
Push-Location $repoRoot
uv run pyinstaller reviewer.spec --noconfirm --clean --distpath $packageOutput.DistPath
Pop-Location

if (-not (Test-Path $packageOutput.OutputExe)) {
    throw "Packaging failed: $($packageOutput.OutputExe) was not created."
}

Write-Host "==> Done"
Write-Host "Output: $($packageOutput.OutputExe)"
