<#
.SYNOPSIS
    Remove YY Torrent for the current user.

.DESCRIPTION
    Reverses install.ps1: unregisters the associations, deletes the shortcuts
    and removes the program folder. Downloaded files are never touched.

    Torrent state (%APPDATA%\YYTorrent) is kept unless -PurgeData is given, so
    reinstalling picks up exactly where you left off.

.EXAMPLE
    .\scripts\uninstall.ps1
    .\scripts\uninstall.ps1 -PurgeData
#>
[CmdletBinding()]
param(
    [switch]$PurgeData
)

$ErrorActionPreference = 'Stop'
$target = Join-Path $env:LOCALAPPDATA 'Programs\YYTorrent'
$exe = Join-Path $target 'YYTorrent.exe'

Get-Process YYTorrent -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "Stopping running YY Torrent (pid $($_.Id))..." -ForegroundColor Yellow
    $_ | Stop-Process -Force
    Start-Sleep -Milliseconds 900
}

if (Test-Path $exe) {
    Write-Host 'Removing file associations...' -ForegroundColor Cyan
    try { & $exe --unregister-shell | Out-Null } catch { Write-Warning "Association cleanup failed: $_" }
}

Write-Host 'Removing shortcuts...' -ForegroundColor Cyan
@(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'YY Torrent.lnk'),
    (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs\YY Torrent.lnk')
) | ForEach-Object {
    if (Test-Path $_) { Remove-Item $_ -Force; Write-Host "  removed $_" -ForegroundColor DarkGray }
}

$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
if (Get-ItemProperty -Path $runKey -Name 'YYTorrent' -ErrorAction SilentlyContinue) {
    Remove-ItemProperty -Path $runKey -Name 'YYTorrent' -Force
    Write-Host '  removed startup entry' -ForegroundColor DarkGray
}

if (Test-Path $target) {
    Write-Host "Removing $target ..." -ForegroundColor Cyan
    Remove-Item $target -Recurse -Force
}

$data = Join-Path $env:APPDATA 'YYTorrent'
if ($PurgeData) {
    if (Test-Path $data) {
        Write-Host "Removing torrent state at $data ..." -ForegroundColor Yellow
        Remove-Item $data -Recurse -Force
    }
} elseif (Test-Path $data) {
    Write-Host ''
    Write-Host "Torrent state kept at $data" -ForegroundColor DarkGray
    Write-Host 'Re-run with -PurgeData to delete it too.' -ForegroundColor DarkGray
}

Write-Host ''
Write-Host 'Uninstalled. Downloaded files were not touched.' -ForegroundColor Green
