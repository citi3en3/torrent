<#
.SYNOPSIS
    Build YYTorrent.exe with PyInstaller.

.EXAMPLE
    .\scripts\build_exe.ps1
    .\scripts\build_exe.ps1 -Clean
#>
[CmdletBinding()]
param(
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    throw "Virtual environment not found at $python. Run: python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
}

Set-Location $root

# The icon is generated rather than checked in, so make sure it exists.
if (-not (Test-Path 'resources\icon.ico')) {
    Write-Host 'Generating icon...' -ForegroundColor Cyan
    & $python 'scripts\make_icon.py'
}

if ($Clean) {
    Write-Host 'Cleaning build artefacts...' -ForegroundColor Cyan
    foreach ($dir in 'build', 'dist') {
        if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }
    }
}

# A running instance holds YYTorrent.exe open and the build would fail on it.
Get-Process YYTorrent -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "Stopping running YY Torrent (pid $($_.Id))..." -ForegroundColor Yellow
    $_ | Stop-Process -Force
    Start-Sleep -Milliseconds 700
}

Write-Host 'Running PyInstaller...' -ForegroundColor Cyan
& $python -m PyInstaller --noconfirm 'torrentapp.spec'
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$exe = Join-Path $root 'dist\YYTorrent\YYTorrent.exe'
if (-not (Test-Path $exe)) { throw "Expected $exe but it was not produced" }

$size = (Get-ChildItem 'dist\YYTorrent' -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ''
Write-Host "Built: $exe" -ForegroundColor Green
Write-Host ("Bundle size: {0:N1} MB" -f ($size / 1MB)) -ForegroundColor Green
Write-Host ''
Write-Host 'Install it with:  .\scripts\install.ps1' -ForegroundColor Cyan
