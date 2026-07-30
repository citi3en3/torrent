<#
.SYNOPSIS
    Install YY Torrent for the current user.

.DESCRIPTION
    Copies the built bundle to %LOCALAPPDATA%\Programs\YYTorrent, creates the
    Desktop and Start Menu shortcuts, and registers the file associations.

    Everything is per-user, so no administrator rights are needed and nothing
    outside this account is touched.

    Note on .torrent: Windows hash-signs the "default handler" choice, so no
    installer can set it programmatically. This registers the app properly
    (Open with + Settings > Default apps) and magnet: links are claimed
    outright; making YY Torrent the .torrent default is a manual two-click step
    the app will prompt for.

.EXAMPLE
    .\scripts\install.ps1
    .\scripts\install.ps1 -StartWithWindows
#>
[CmdletBinding()]
param(
    [switch]$StartWithWindows,
    [switch]$NoShortcuts
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root 'dist\YYTorrent'
$target = Join-Path $env:LOCALAPPDATA 'Programs\YYTorrent'
$exe = Join-Path $target 'YYTorrent.exe'

if (-not (Test-Path (Join-Path $source 'YYTorrent.exe'))) {
    throw "No build found at $source. Run .\scripts\build_exe.ps1 first."
}

Get-Process YYTorrent -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "Stopping running YY Torrent (pid $($_.Id))..." -ForegroundColor Yellow
    $_ | Stop-Process -Force
    Start-Sleep -Milliseconds 900
}

Write-Host "Installing to $target ..." -ForegroundColor Cyan
if (Test-Path $target) { Remove-Item $target -Recurse -Force }
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item -Path (Join-Path $source '*') -Destination $target -Recurse -Force

if (-not $NoShortcuts) {
    $shell = New-Object -ComObject WScript.Shell
    $icon = Join-Path $target 'resources\icon.ico'
    if (-not (Test-Path $icon)) { $icon = $exe }

    $links = @(
        (Join-Path ([Environment]::GetFolderPath('Desktop')) 'YY Torrent.lnk'),
        (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs\YY Torrent.lnk')
    )
    foreach ($link in $links) {
        $parent = Split-Path -Parent $link
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
        $shortcut = $shell.CreateShortcut($link)
        $shortcut.TargetPath = $exe
        $shortcut.WorkingDirectory = $target
        $shortcut.IconLocation = "$icon,0"
        $shortcut.Description = 'BitTorrent client'
        $shortcut.Save()
        Write-Host "  shortcut: $link" -ForegroundColor DarkGray
    }
}

Write-Host 'Registering file associations...' -ForegroundColor Cyan
& $exe --register-shell | Out-Null

if ($StartWithWindows) {
    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    New-ItemProperty -Path $runKey -Name 'YYTorrent' -Value "`"$exe`" --hidden" -PropertyType String -Force | Out-Null
    Write-Host '  will start with Windows (minimised to tray)' -ForegroundColor DarkGray
}

Write-Host ''
Write-Host 'Installed.' -ForegroundColor Green
Write-Host "  Program:  $exe"
Write-Host "  Settings: $env:APPDATA\YYTorrent"
Write-Host ''
Write-Host 'magnet: links now open YY Torrent.' -ForegroundColor Green
Write-Host 'For .torrent files, right-click one > Open with > Choose another app >' -ForegroundColor Yellow
Write-Host 'pick YY Torrent and tick "Always use this app". Windows does not allow' -ForegroundColor Yellow
Write-Host 'any program to set that automatically.' -ForegroundColor Yellow
