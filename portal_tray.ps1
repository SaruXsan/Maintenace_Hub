Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$pythonExe = "python"
if (Test-Path ".venv\Scripts\python.exe") {
    $pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
}

$uvicornArgs = "-m uvicorn app.main:app --host 127.0.0.1 --port 8000"
$server = Start-Process -FilePath $pythonExe -ArgumentList $uvicornArgs -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru

$notifyIcon = New-Object System.Windows.Forms.NotifyIcon
$notifyIcon.Icon = [System.Drawing.SystemIcons]::Application
$notifyIcon.Text = "Maintenance Hub Portal"
$notifyIcon.Visible = $true

$contextMenu = New-Object System.Windows.Forms.ContextMenuStrip
$openItem = $contextMenu.Items.Add("Open Web")
$exitItem = $contextMenu.Items.Add("Exit")
$notifyIcon.ContextMenuStrip = $contextMenu

$openHandler = {
    Start-Process "http://127.0.0.1:8000"
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
$exitItem.add_Click($exitHandler)
$notifyIcon.add_DoubleClick($openHandler)

Start-Sleep -Milliseconds 1200
Start-Process "http://127.0.0.1:8000"

[System.Windows.Forms.Application]::Run()
