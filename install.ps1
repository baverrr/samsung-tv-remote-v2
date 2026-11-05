# Installs Samsung & Apple TV Remote for the current Windows user: a venv, the Start menu shortcut, tvremote:// links,
# the tv-remote command and (with -Bridge) the HTTP bridge that ESP32 and other network remotes send their
# keys to, started at logon. The Windows counterpart of install.sh; mirroring needs no helper here
# (Windows' own Cast panel sends the sound too), so there's no -Audio.
# Run it from the checkout, wherever it lives; run it again after moving the checkout.
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 [-Bridge]
param([switch]$Bridge)
$ErrorActionPreference = 'Stop'

$AppDir = $PSScriptRoot
$BinDir = Join-Path $env:LOCALAPPDATA 'Programs\tv-remote\bin'
# The paths go into a .cmd file, a shortcut and a registry command; these characters would need different
# escaping in each.
foreach ($dir in @($AppDir, $BinDir)) {
    if ($dir -match '["%^&|<>]') {
        throw "Can't install from ${dir}: move the checkout to a path without "" % ^ & | < or >"
    }
}

function Find-Python {
    # The py launcher first (python.org), then python (winget, or on PATH). The Microsoft Store's
    # "python" stand-in only opens the Store: it fails the version check and is skipped.
    foreach ($candidate in @(@('py', '-3'), @('python'))) {
        if (-not (Get-Command $candidate[0] -ErrorAction SilentlyContinue)) { continue }
        $rest = @($candidate | Select-Object -Skip 1)
        try {  # under 'Stop', Windows PowerShell turns anything the stand-in writes to stderr into an error
            $version = & $candidate[0] @rest -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        } catch { continue }
        if ($LASTEXITCODE -eq 0 -and $version -and [version]$version -ge [version]'3.10') { return ,$candidate }
    }
    throw 'Samsung & Apple TV Remote needs Python 3.10 or later: winget install Python.Python.3.13 (or python.org), then run this again'
}

$Venv = Join-Path $AppDir 'venv'
$Python = Join-Path $Venv 'Scripts\python.exe'
$PythonW = Join-Path $Venv 'Scripts\pythonw.exe'
$Main = Join-Path $AppDir 'tv_remote.py'

Write-Host "venv: $Venv"
if (-not (Test-Path $Python)) {
    $py = Find-Python
    $rest = @($py | Select-Object -Skip 1)
    & $py[0] @rest -m venv $Venv
    if ($LASTEXITCODE) { throw 'Creating the venv failed' }
}
# PySide6 (the window), pyatv (the Apple TV remote). srptools 2 made an argument pyatv 0.18 passes
# keyword-only, which breaks pairing, so it stays on 1.x. biplist and patch-ng: the AirPlay patch below.
& $Python -m pip install --quiet --disable-pip-version-check PySide6-Essentials websocket-client `
    'pyatv==0.18.0' 'srptools==1.0.1' 'biplist==1.0.3' 'patch-ng==1.18.1'
if ($LASTEXITCODE) { throw 'Installing the Python packages failed' }
# AirPlay casting to tvOS 26+ needs an unreleased pyatv fix (see the patch's header). Applied once, all or
# nothing; without it casting to an Apple TV stays off and says why.
& $Python -I (Join-Path $AppDir 'tools\patch_pyatv.py')

$Icon = Join-Path $AppDir 'tv-remote.ico'
& $Python -I (Join-Path $AppDir 'tools\make_icon.py') $Icon
if ($LASTEXITCODE) { throw 'Making the icon failed' }

$AppName = 'Samsung & Apple TV Remote'
$Shortcut = Join-Path ([Environment]::GetFolderPath('Programs')) "$AppName.lnk"
Write-Host "Start menu: $Shortcut"
$link = (New-Object -ComObject WScript.Shell).CreateShortcut($Shortcut)
$link.TargetPath = $PythonW  # pythonw: no console window behind the remote
$link.Arguments = "-I ""$Main"""
$link.WorkingDirectory = $AppDir
$link.IconLocation = $Icon
$link.Description = 'Remote control for Samsung TVs and Apple TV'
$link.Save()

# tvremote:// links (a dashboard tile) open the remote; tvremote://cast also opens the cast window.
$Scheme = 'HKCU:\Software\Classes\tvremote'
Write-Host 'links: tvremote://'
New-Item -Path $Scheme -Value "URL:$AppName" -Force | Out-Null
New-ItemProperty -Path $Scheme -Name 'URL Protocol' -Value '' -Force | Out-Null
New-Item -Path "$Scheme\DefaultIcon" -Value $Icon -Force | Out-Null
New-Item -Path "$Scheme\shell\open\command" -Value """$PythonW"" -I ""$Main"" ""%1""" -Force | Out-Null

Write-Host "command: $BinDir\tv-remote.cmd"
New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
Set-Content -Path (Join-Path $BinDir 'tv-remote.cmd') -Encoding Oem -Value @(  # cmd reads the OEM code page
    '@echo off',
    "rem Installed by install.ps1, pointing at the checkout it ran from.",
    """$Python"" -I ""$Main"" %*"
)
# The user PATH as stored (with %VARIABLES% unexpanded), so adding to it doesn't freeze them.
$envKey = Get-Item 'HKCU:\Environment'
$userPath = $envKey.GetValue('Path', '', 'DoNotExpandEnvironmentNames')
if (-not (($userPath -split ';') -contains $BinDir)) {
    $newPath = (($userPath.TrimEnd(';'), $BinDir) | Where-Object { $_ }) -join ';'
    Set-ItemProperty -Path 'HKCU:\Environment' -Name 'Path' -Value $newPath -Type ExpandString
    # Tell Explorer, so terminals opened from now on find tv-remote.
    Add-Type -Namespace TvRemote -Name Native -MemberDefinition @'
[DllImport("user32.dll", CharSet = CharSet.Unicode)]
public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam,
    uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
'@
    $result = [UIntPtr]::Zero
    [TvRemote.Native]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, 'Environment', 2, 5000,
        [ref]$result) | Out-Null
    Write-Host "Note: added $BinDir to your PATH; open a new terminal to use tv-remote."
}

$Task = "$AppName bridge"
if ($Bridge) {
    Write-Host "bridge: scheduled task '$Task' at logon (port 8198; token: tv-remote serve --token)"
    $action = New-ScheduledTaskAction -Execute $PythonW -Argument "-I ""$Main"" serve" -WorkingDirectory $AppDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $Task -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue  # picks up a moved checkout or new code
    Start-ScheduledTask -TaskName $Task
}

# The TV fetches casts from this PC (TCP 8199, and the Apple TV's clock sync on UDP 8199), and network
# remotes reach the bridge on 8198. Windows asks the first time either listens; these rules answer it for
# the local network only.
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
# The bridge's port stays open while its task is installed, also when this runs again without -Bridge.
$hasBridge = $Bridge -or [bool](Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue)
$ports = if ($hasBridge) { '8198,8199' } else { '8199' }
$rules = @(
    "New-NetFirewallRule -DisplayName '$AppName (TCP)' -Direction Inbound -Protocol TCP -LocalPort $ports -RemoteAddress LocalSubnet -Profile Private -Action Allow",
    "New-NetFirewallRule -DisplayName '$AppName (UDP)' -Direction Inbound -Protocol UDP -LocalPort 8199 -RemoteAddress LocalSubnet -Profile Private -Action Allow"
)
if ($admin) {
    Remove-NetFirewallRule -DisplayName "$AppName (TCP)", "$AppName (UDP)" -ErrorAction SilentlyContinue
    foreach ($rule in $rules) { Invoke-Expression $rule | Out-Null }
    Write-Host "firewall: $AppName may take connections from your local network (TCP $ports, UDP 8199)"
} else {
    Write-Host 'Note: to let the TV reach casts (and remotes reach the bridge), run these once in an administrator PowerShell:'
    foreach ($rule in $rules) { Write-Host "  $rule" }
    Write-Host '  Or allow Python on Private networks when Windows asks.'
}
Write-Host "Done. Set your TV's address in $env:APPDATA\tv-remote\config.json (see README), then open $AppName from the Start menu."
