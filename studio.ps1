<#
.SYNOPSIS
  Start/stop the RHEL 10 Image Mode Studio web server on Windows.
.EXAMPLE
  .\studio.ps1 start      # launch in the background, print the URL
  .\studio.ps1 stop       # stop it
  .\studio.ps1 restart
  .\studio.ps1 status
  .\studio.ps1 logs       # tail the log
#>
param(
  [Parameter(Position = 0)]
  [ValidateSet('start', 'stop', 'restart', 'status', 'logs')]
  [string]$Action = 'status'
)

$Here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$App     = Join-Path $Here 'bootc-builder-server.py'
$PidFile = Join-Path $Here '.studio.pid'
$LogFile = Join-Path $Here 'studio.log'
$Port    = 8080

function Get-Python {
  foreach ($name in @('python', 'python3', 'py')) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
  }
  return $null
}

function Test-Running {
  if (-not (Test-Path $PidFile)) { return $false }
  $procId = Get-Content $PidFile -ErrorAction SilentlyContinue
  if (-not $procId) { return $false }
  return [bool](Get-Process -Id $procId -ErrorAction SilentlyContinue)
}

function Show-Url {
  Write-Host "  -> http://localhost:$Port"
  if (Test-Path $LogFile) {
    $lan = Select-String -Path $LogFile -Pattern 'http://[0-9.]+:[0-9]+' -ErrorAction SilentlyContinue |
           Select-Object -Last 1
    if ($lan) { Write-Host "  -> $($lan.Matches[0].Value)   (from other machines on your network)" }
  }
}

function Start-Studio {
  if (Test-Running) {
    Write-Host "Image Mode Studio already running (pid $(Get-Content $PidFile))."
    Show-Url; return
  }
  $py = Get-Python
  if (-not $py)            { Write-Host "X python not found on PATH."; exit 1 }
  if (-not (Test-Path $App)) { Write-Host "X $App not found."; exit 1 }
  Write-Host "Starting Image Mode Studio..."
  $p = Start-Process -FilePath $py -ArgumentList "`"$App`"" -RedirectStandardOutput $LogFile `
         -RedirectStandardError "$LogFile.err" -WindowStyle Hidden -PassThru
  $p.Id | Out-File -FilePath $PidFile -Encoding ascii
  Start-Sleep -Seconds 1
  if (Test-Running) {
    Write-Host "OK Started (pid $($p.Id))."
    Show-Url
    Write-Host "  logs: .\studio.ps1 logs"
  } else {
    Write-Host "X Failed to start — last log lines:"
    if (Test-Path $LogFile) { Get-Content $LogFile -Tail 20 }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
    exit 1
  }
}

function Stop-Studio {
  if (-not (Test-Running)) { Write-Host "Not running."; Remove-Item $PidFile -ErrorAction SilentlyContinue; return }
  $procId = Get-Content $PidFile
  Write-Host "Stopping (pid $procId)..."
  Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
  Remove-Item $PidFile -ErrorAction SilentlyContinue
  Write-Host "OK Stopped."
}

switch ($Action) {
  'start'   { Start-Studio }
  'stop'    { Stop-Studio }
  'restart' { Stop-Studio; Start-Studio }
  'status'  { if (Test-Running) { Write-Host "running (pid $(Get-Content $PidFile))"; Show-Url } else { Write-Host "stopped" } }
  'logs'    { if (Test-Path $LogFile) { Get-Content $LogFile -Wait -Tail 40 } else { Write-Host "no log yet" } }
}
