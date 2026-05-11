Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$pythonExe = "python"
if (Test-Path ".venv\Scripts\python.exe") {
    $pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
}

function Get-ExistingMaintenanceHubPort {
    $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^python(\.exe)?$' -and $_.CommandLine -match 'uvicorn app\.main:app' }
    if (-not $procs) { return $null }
    $ports = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.OwningProcess -in $procs.ProcessId } |
        Select-Object -ExpandProperty LocalPort -Unique |
        Sort-Object
    if ($ports -and $ports.Count -gt 0) { return $ports[0] }
    return $null
}

function Get-FreePort {
    param(
        [int]$Start = 8100,
        [int]$End = 8199
    )

    $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    $used = @{}
    foreach ($listener in $listeners) {
        $used[$listener.Port] = $true
    }

    for ($p = $Start; $p -le $End; $p++) {
        if (-not $used.ContainsKey($p)) {
            return $p
        }
    }
    return 8765
}

$existingPort = Get-ExistingMaintenanceHubPort
if ($existingPort) {
    Start-Process "http://127.0.0.1:$existingPort"
    exit 0
}

$port = Get-FreePort
$webUrl = "http://127.0.0.1:$port"
$lanIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.PrefixOrigin -ne 'WellKnown' } |
    Select-Object -First 1 -ExpandProperty IPAddress)
if (-not $lanIp) {
    $lanIp = "YOUR_PC_IP"
}
$networkUrl = "http://$lanIp:$port"

$uvicornArgs = "-m uvicorn app.main:app --host 0.0.0.0 --port $port"
$server = Start-Process -FilePath $pythonExe -ArgumentList $uvicornArgs -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru

$notifyIcon = New-Object System.Windows.Forms.NotifyIcon
$notifyIcon.Icon = [System.Drawing.SystemIcons]::Application
$notifyIcon.Text = "Maintenance Hub Portal ($port)"
$notifyIcon.Visible = $true

$contextMenu = New-Object System.Windows.Forms.ContextMenuStrip
$openItem = $contextMenu.Items.Add("Open Web")
$openNetworkItem = $contextMenu.Items.Add("Open Network URL")
$exitItem = $contextMenu.Items.Add("Exit")
$notifyIcon.ContextMenuStrip = $contextMenu

$openHandler = {
    Start-Process $webUrl
}

$openNetworkHandler = {
    Start-Process $networkUrl
}

$exitHandler = {
    try {
        if ($server -and -not $server.HasExited) {
            Stop-Process -Id $server.Id -Force
        }
    } catch {}

    $notifyIcon.Visible = $false
    $notifyIcon.Dispose()
    [System.Windows.Forms.Application]::Exit()
}

$openItem.add_Click($openHandler)
$openNetworkItem.add_Click($openNetworkHandler)
$exitItem.add_Click($exitHandler)
$notifyIcon.add_DoubleClick($openHandler)

Start-Sleep -Milliseconds 1200
Start-Process $webUrl

[System.Windows.Forms.Application]::Run()
